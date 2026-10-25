from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from statistics import median
from time import monotonic, time
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.storage import Store

from .const import CONTROL_LOOP_STALL_TIME, DOMAIN, BoilerStatus, FlameStatus, PWMStatus

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant

    from .boiler import BoilerState
    from .pwm import PWMState

_LOGGER = logging.getLogger(__name__)


def flame_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    return Store(hass, 1, f"{DOMAIN}.{entry_id}.flame")


@dataclass(frozen=True, slots=True)
class FlameState:
    is_active: bool
    is_inactive: bool
    health_status: FlameStatus

    latest_on_time_seconds: float | None
    average_on_time_seconds: float | None
    last_cycle_duration_seconds: float | None

    sample_count_4h: int
    cycles_last_hour: float
    duty_ratio_last_15m: float
    median_on_duration_seconds_4h: float | None

    last_cut_short_peak_flow: float | None
    last_cut_short_setpoint: float | None
    cut_short_burn_ends: tuple[float, ...]


class Flame:
    """Tracks boiler flame on/off, maintains rolling statistics, and classifies health."""

    # Thresholds
    MAX_CYCLES_PER_HOUR_PWM: float = 8.0
    MAX_CYCLES_PER_HOUR_NON_PWM: float = 3.0
    MAX_CYCLES_PER_HOUR_MODULATING_STEADY: float = 2.0
    PWM_SHORT_BURNS_THRESHOLD: int = 2

    STUCK_OFF_SECONDS: float = 300.0
    STUCK_ON_WITHOUT_DEMAND_SECONDS: float = 120.0
    # A Remeha cuts out about 5 °C above the control setpoint
    IGNITION_OVERSHOOT_MARGIN: float = 4.0
    # The cut-out ends the burn 6 to 11 s after the peak; listeners are notified every 5 s
    IGNITION_CUT_OUT_SECONDS: float = 30.0
    # A hot water request can end before the listener batch that first shows the flame
    HOT_WATER_LEAD_SECONDS: float = 30.0
    EXPECTED_MODULATING_DUTY_RATIO_WHEN_HEATING: float = 0.60

    # Windows
    DUTY_WINDOW_SECONDS: int = 15 * 60
    CYCLES_WINDOW_SECONDS: int = 60 * 60
    MEDIAN_WINDOW_SECONDS: int = 4 * 60 * 60

    # Debounce
    MEDIAN_TOLERANCE: float = 0.9
    MIN_ON_SAMPLES_FOR_HEALTH: int = 3
    MAX_DOMESTIC_HOT_WATER_IDLE_OFF_SECONDS: float = 30.0

    # Status buckets
    _TRANSIENT_STATUSES: Iterable[BoilerStatus] = (
        BoilerStatus.PREHEATING,
        BoilerStatus.PUMP_STARTING,
    )

    _HEATING_STATUSES: Iterable[BoilerStatus] = (
        BoilerStatus.HEATING_UP,
        BoilerStatus.NEAR_SETPOINT,
        BoilerStatus.AT_SETPOINT,
        BoilerStatus.COOLING_DOWN,
        BoilerStatus.BELOW_SETPOINT,
        BoilerStatus.OVERSHOOT_HANDLING,
        BoilerStatus.OVERSHOOT_STABILIZED,
    )

    def __init__(self, smoothing_alpha: float = 0.2) -> None:
        if not (0.0 <= smoothing_alpha <= 1.0):
            msg = "smoothing_alpha must be within [0.0, 1.0]."
            raise ValueError(msg)

        self._smoothing_alpha: float = smoothing_alpha
        self._store: Store[dict[str, Any]] | None = None

        # Timing internals
        self._latest_on_time_seconds: float = 0.0
        self._average_on_time_seconds: float | None = None
        self._last_cycle_duration_seconds: float | None = None

        self._has_completed_first_cycle: bool = False
        self._flame_on_monotonic: float | None = None
        self._flame_off_monotonic: float | None = None
        self._demand_since_monotonic: float | None = None
        self._pwm_planned_on_seconds: float | None = None
        self._burn_peak_flow: float | None = None
        self._burn_peak_monotonic: float | None = None
        self._burn_hot_water: bool = False
        self._hot_water_seen_monotonic: float | None = None
        self._burn_setpoint: float | None = None
        self._last_cut_short_peak_flow: float | None = None
        self._last_cut_short_setpoint: float | None = None

        # Stored states
        self._last_boiler_state: BoilerState | None = None
        self._last_pulse_width_modulation_state: PWMState | None = None
        self._pwm_state_updated_monotonic: float | None = None

        # Rolling windows
        self._last_update_monotonic: float | None = None
        self._cycle_end_times_window: deque[float] = deque()
        self._on_deltas_window: deque[tuple[float, float]] = deque()
        self._on_durations_window: deque[tuple[float, float]] = deque()
        self._pwm_burns_cut_short: deque[tuple[float, bool]] = deque(maxlen=3)

        # Health
        self._health_status: FlameStatus = FlameStatus.INSUFFICIENT_DATA

    async def async_initialize(self, hass: HomeAssistant, entry_id: str) -> None:
        # The climate is re-added without a reload when its entity id changes,
        # and the windows already hold everything stored
        if self._store is not None:
            return

        self._store = flame_store(hass, entry_id)

        if (data := await self._store.async_load()) is None:
            return

        # Stored as wall-clock times: monotonic time restarts with the process
        offset = monotonic() - time()

        self._average_on_time_seconds = data.get("average_on_time_seconds")
        self._last_cycle_duration_seconds = data.get("last_cycle_duration_seconds")
        self._last_cut_short_peak_flow = data.get("last_cut_short_peak_flow")
        self._last_cut_short_setpoint = data.get("last_cut_short_setpoint")
        self._has_completed_first_cycle = self._average_on_time_seconds is not None

        def stored(key: str) -> list[tuple[float, Any]]:
            return [(ended + offset, value) for ended, value in data.get(key, [])]

        # Anything recorded since setup goes after the stored entries,
        # so a full bounded window drops the oldest
        self._cycle_end_times_window = deque(
            [
                *(ended + offset for ended in data.get("cycle_end_times", [])),
                *self._cycle_end_times_window,
            ]
        )
        self._on_durations_window = deque([*stored("on_durations"), *self._on_durations_window])
        self._on_deltas_window = deque([*stored("on_deltas"), *self._on_deltas_window])
        self._pwm_burns_cut_short = deque(
            [*stored("pwm_burns_cut_short"), *self._pwm_burns_cut_short],
            maxlen=self._pwm_burns_cut_short.maxlen,
        )

        _LOGGER.debug("Loaded %d flame cycles from storage.", len(self._on_durations_window))

    def _data_to_save(self) -> dict[str, Any]:
        offset = time() - monotonic()

        return {
            "average_on_time_seconds": self._average_on_time_seconds,
            "last_cycle_duration_seconds": self._last_cycle_duration_seconds,
            "last_cut_short_peak_flow": self._last_cut_short_peak_flow,
            "last_cut_short_setpoint": self._last_cut_short_setpoint,
            "cycle_end_times": [ended + offset for ended in self._cycle_end_times_window],
            "on_durations": [(ended + offset, value) for ended, value in self._on_durations_window],
            "on_deltas": [(ended + offset, value) for ended, value in self._on_deltas_window],
            "pwm_burns_cut_short": [
                (ended + offset, value) for ended, value in self._pwm_burns_cut_short
            ],
        }

    @property
    def health_status(self) -> FlameStatus:
        return self._health_status

    @property
    def is_active(self) -> bool:
        return self._is_flame_active_internal()

    @property
    def is_inactive(self) -> bool:
        return not self._is_flame_active_internal()

    @property
    def on_since(self) -> float | None:
        if self._is_flame_active_internal() and self._flame_on_monotonic is not None:
            return self._flame_on_monotonic

        return None

    @property
    def latest_on_time_seconds(self) -> float | None:
        if self._is_flame_active_internal() and self._flame_on_monotonic is not None:
            return self._latest_on_time_seconds

        return None

    @property
    def average_on_time_seconds(self) -> float | None:
        return self._average_on_time_seconds if self._has_completed_first_cycle else None

    @property
    def last_cycle_duration_seconds(self) -> float | None:
        return self._last_cycle_duration_seconds

    @property
    def last_cut_short_peak_flow(self) -> float | None:
        return self._last_cut_short_peak_flow

    @property
    def last_cut_short_setpoint(self) -> float | None:
        return self._last_cut_short_setpoint

    @property
    def cut_short_burn_ends(self) -> tuple[float, ...]:
        return tuple(ended for ended, cut_short in self._pwm_burns_cut_short if cut_short)

    @property
    def cycles_last_hour(self) -> float:
        return self._cycles_per_hour(monotonic())

    @property
    def duty_ratio_last_15m(self) -> float:
        return self._duty_ratio_last_window(monotonic())

    @property
    def median_on_duration_seconds_4h(self) -> float | None:
        median_value, _ = self._median_on_duration(monotonic())
        return median_value

    @property
    def sample_count_4h(self) -> int:
        _, count = self._median_on_duration(monotonic())
        return count

    def update(self, boiler_state: BoilerState, pwm_state: PWMState | None = None) -> None:

        now = monotonic()

        # Initialize the last update time if this is the first update
        if self._last_update_monotonic is None:
            self._last_update_monotonic = now

        # Accumulate duty time since the last tick when the previous flame state was ON
        currently_active = bool(boiler_state.flame_active)
        previously_active = self._is_flame_active_internal()
        elapsed = max(0.0, now - self._last_update_monotonic)

        # Update internal state tracking
        self._last_boiler_state = boiler_state
        if pwm_state is not None:
            self._last_pulse_width_modulation_state = pwm_state
            self._pwm_state_updated_monotonic = now
        if boiler_state.hot_water_active:
            self._hot_water_seen_monotonic = now

        if previously_active and elapsed > 0.0:
            self._on_deltas_window.append((now, elapsed))

        self._prune_duty_window(now)

        # OFF -> ON
        if currently_active and not previously_active:
            pwm = self._fresh_pulse_width_modulation_state(now)
            hot_water = (
                self._hot_water_seen_monotonic is not None
                and now - self._hot_water_seen_monotonic <= self.HOT_WATER_LEAD_SECONDS
                and (
                    self._flame_off_monotonic is None
                    or self._hot_water_seen_monotonic > self._flame_off_monotonic
                )
            )
            pwm_burn = (
                pwm is not None
                and pwm.status == PWMStatus.ON
                and pwm.duty_cycle is not None
                and not hot_water
            )
            self._pwm_planned_on_seconds = pwm.duty_cycle[0] if pwm_burn else None
            self._flame_on_monotonic = now
            self._flame_off_monotonic = None
            self._burn_peak_flow = boiler_state.flow_temperature
            self._burn_peak_monotonic = now
            self._burn_hot_water = hot_water
            self._burn_setpoint = boiler_state.setpoint
            self._last_update_monotonic = now
            self._recompute_health(now)

            _LOGGER.debug("Flame transition OFF->ON")

            return

        # ON -> ON
        if currently_active and self._flame_on_monotonic is not None:
            self._latest_on_time_seconds = now - self._flame_on_monotonic
            self._burn_setpoint = boiler_state.setpoint
            if boiler_state.hot_water_active:
                self._burn_hot_water = True
                self._pwm_planned_on_seconds = None
            if boiler_state.flow_temperature is not None and (
                self._burn_peak_flow is None
                or boiler_state.flow_temperature >= self._burn_peak_flow
            ):
                self._burn_peak_flow = boiler_state.flow_temperature
                self._burn_peak_monotonic = now

            self._last_update_monotonic = now
            self._recompute_health(now)
            return

        # ON -> OFF
        if not currently_active and previously_active:
            duration = (
                (now - self._flame_on_monotonic) if self._flame_on_monotonic is not None else 0.0
            )

            self._flame_on_monotonic = None
            self._flame_off_monotonic = now
            self._prune_cycles_window(now)
            self._prune_median_window(now)

            # Hot water keep-warm burns last seconds by design and say nothing about heating cycles
            if not self._burn_hot_water:
                self._last_cycle_duration_seconds = duration
                self._average_on_time_seconds = (
                    duration
                    if self._average_on_time_seconds is None
                    else (1.0 - self._smoothing_alpha) * self._average_on_time_seconds
                    + self._smoothing_alpha * duration
                )
                self._cycle_end_times_window.append(now)
                self._on_durations_window.append((now, duration))

            # A burn that ends while PWM is still ON was ended by the boiler, not by PWM
            pwm = self._fresh_pulse_width_modulation_state(now)
            if self._pwm_planned_on_seconds is not None and pwm is not None:
                cut_short = pwm.status == PWMStatus.ON and duration < self._pwm_planned_on_seconds
                self._pwm_burns_cut_short.append((now, cut_short))

                if (
                    cut_short
                    and self._burn_peak_flow is not None
                    and self._burn_setpoint is not None
                    and self._burn_peak_flow > self._burn_setpoint + self.IGNITION_OVERSHOOT_MARGIN
                    and self._burn_peak_monotonic is not None
                    and now - self._burn_peak_monotonic <= self.IGNITION_CUT_OUT_SECONDS
                ):
                    self._last_cut_short_peak_flow = self._burn_peak_flow
                    self._last_cut_short_setpoint = self._burn_setpoint
                    _LOGGER.warning(
                        "The boiler stopped a PWM burn after %.0fs with the flow at %.1f°C "
                        "against a %.1f°C setpoint; raise the overshoot protection value",
                        duration,
                        self._burn_peak_flow,
                        self._burn_setpoint,
                    )

            self._pwm_planned_on_seconds = None

            self._has_completed_first_cycle = True
            self._last_update_monotonic = now

            if self._store:
                self._store.async_delay_save(self._data_to_save)

            _LOGGER.debug(
                "Flame transition ON->OFF: duration=%.1fs, cycles_last_hour=%.1f, samples_4h=%d",
                duration,
                self._cycles_per_hour(now),
                self.sample_count_4h,
            )

            self._recompute_health(now)
            return

        # OFF -> OFF
        if not currently_active:
            if self._flame_off_monotonic is None:
                self._flame_off_monotonic = now

            self._last_update_monotonic = now
            self._recompute_health(now)
            return

    def _recompute_health(self, now: float) -> None:
        state = self._last_boiler_state
        if state is None:
            self._health_status = FlameStatus.INSUFFICIENT_DATA
            return

        domestic_hot_water_active = bool(state.hot_water_active)
        heating_demand = bool(state.is_active) or (state.status in self._HEATING_STATUSES)

        if not heating_demand and not domestic_hot_water_active:
            self._demand_since_monotonic = None
        elif self._demand_since_monotonic is None:
            self._demand_since_monotonic = now

        # The flame can only be late since the demand started, not since it last went out
        last_on_seconds = (
            self._latest_on_time_seconds
            if (state.flame_active and self._flame_on_monotonic is not None)
            else 0.0
        )
        last_off_seconds = (
            (now - max(self._flame_off_monotonic, self._demand_since_monotonic or 0.0))
            if (not state.flame_active and self._flame_off_monotonic is not None)
            else 0.0
        )

        cycles_per_hour = self._cycles_per_hour(now)
        duty_ratio = self._duty_ratio_last_window(now)
        median_on_seconds, sample_count = self._median_on_duration(now)

        modulating_boiler = state.relative_modulation_level is not None and isinstance(
            state.relative_modulation_level, (int, float)
        )

        # Thin history: still allow stuck checks, avoid cycling judgments
        if sample_count < self.MIN_ON_SAMPLES_FOR_HEALTH:
            if not heating_demand and not domestic_hot_water_active:
                if state.flame_active and last_on_seconds > self.STUCK_ON_WITHOUT_DEMAND_SECONDS:
                    self._health_status = FlameStatus.STUCK_ON
                else:
                    self._health_status = FlameStatus.IDLE_OK

                return

            timeout = (
                self.MAX_DOMESTIC_HOT_WATER_IDLE_OFF_SECONDS
                if domestic_hot_water_active
                else self.STUCK_OFF_SECONDS
            )
            if (
                (not state.flame_active)
                and last_off_seconds > timeout
                and state.status not in self._TRANSIENT_STATUSES
            ):
                self._health_status = FlameStatus.STUCK_OFF
            else:
                self._health_status = FlameStatus.HEALTHY

            return

        # No demand and not domestic hot water
        if not heating_demand and not domestic_hot_water_active:
            if state.flame_active and last_on_seconds > self.STUCK_ON_WITHOUT_DEMAND_SECONDS:
                self._health_status = FlameStatus.STUCK_ON
            else:
                self._health_status = FlameStatus.IDLE_OK

            return

        # Domestic hot water: allow continuous flame;
        # only stuck-off if off too long (and not transient)
        if domestic_hot_water_active:
            if (
                (not state.flame_active)
                and last_off_seconds > self.MAX_DOMESTIC_HOT_WATER_IDLE_OFF_SECONDS
                and state.status not in self._TRANSIENT_STATUSES
            ):
                self._health_status = FlameStatus.STUCK_OFF
            else:
                self._health_status = FlameStatus.HEALTHY

            return

        # Space heating demand
        if (
            (not state.flame_active)
            and last_off_seconds > self.STUCK_OFF_SECONDS
            and state.status not in self._TRANSIENT_STATUSES
        ):
            self._health_status = FlameStatus.STUCK_OFF
            return

        # PWM-driven demand
        if (
            pwm := self._fresh_pulse_width_modulation_state(now)
        ) is not None and pwm.status == PWMStatus.ON:
            if cycles_per_hour > self.MAX_CYCLES_PER_HOUR_PWM:
                self._health_status = FlameStatus.SHORT_CYCLING
                return

            if (
                sum(
                    cut_short
                    for ended, cut_short in self._pwm_burns_cut_short
                    if ended >= now - self.MEDIAN_WINDOW_SECONDS
                )
                >= self.PWM_SHORT_BURNS_THRESHOLD
            ):
                self._health_status = FlameStatus.PWM_SHORT
                return

            self._health_status = FlameStatus.HEALTHY
            return

        # Non-PWM: internal regulation; do not require continuous flame
        if modulating_boiler:
            if (
                cycles_per_hour <= self.MAX_CYCLES_PER_HOUR_MODULATING_STEADY
                and duty_ratio >= 0.8 * self.EXPECTED_MODULATING_DUTY_RATIO_WHEN_HEATING
            ):
                self._health_status = FlameStatus.HEALTHY
                return

            if (
                median_on_seconds is not None
                and median_on_seconds < self.MEDIAN_TOLERANCE * 180
                and cycles_per_hour > self.MAX_CYCLES_PER_HOUR_NON_PWM
            ):
                self._health_status = FlameStatus.SHORT_CYCLING
                return

            self._health_status = FlameStatus.HEALTHY
            return

        # Non-modulating boiler
        if (
            median_on_seconds is not None
            and median_on_seconds < self.MEDIAN_TOLERANCE * 180
            and cycles_per_hour > self.MAX_CYCLES_PER_HOUR_NON_PWM
        ):
            self._health_status = FlameStatus.SHORT_CYCLING
            return

        self._health_status = FlameStatus.HEALTHY

    def _fresh_pulse_width_modulation_state(self, now: float) -> PWMState | None:
        # Only the control loop sends the PWM state,
        # so it goes stale while SAT is off, paused or offline
        if (
            self._pwm_state_updated_monotonic is None
            or now - self._pwm_state_updated_monotonic > CONTROL_LOOP_STALL_TIME.total_seconds()
        ):
            return None

        return self._last_pulse_width_modulation_state

    def _prune_duty_window(self, now: float) -> None:
        cutoff = now - self.DUTY_WINDOW_SECONDS
        while self._on_deltas_window and self._on_deltas_window[0][0] < cutoff:
            self._on_deltas_window.popleft()

    def _prune_cycles_window(self, now: float) -> None:
        cutoff = now - self.CYCLES_WINDOW_SECONDS
        while self._cycle_end_times_window and self._cycle_end_times_window[0] < cutoff:
            self._cycle_end_times_window.popleft()

    def _prune_median_window(self, now: float) -> None:
        cutoff = now - self.MEDIAN_WINDOW_SECONDS
        while self._on_durations_window and self._on_durations_window[0][0] < cutoff:
            self._on_durations_window.popleft()

    def _duty_ratio_last_window(self, now: float) -> float:
        self._prune_duty_window(now)
        on_seconds = sum(delta for _, delta in self._on_deltas_window)
        return min(1.0, max(0.0, on_seconds / float(self.DUTY_WINDOW_SECONDS)))

    def _cycles_per_hour(self, now: float) -> float:
        self._prune_cycles_window(now)
        return float(len(self._cycle_end_times_window))

    def _median_on_duration(self, now: float) -> tuple[float | None, int]:
        self._prune_median_window(now)
        if not self._on_durations_window:
            return None, 0

        durations = [duration for _, duration in self._on_durations_window]
        return float(median(durations)), len(durations)

    def _is_flame_active_internal(self) -> bool:
        return bool(self._last_boiler_state.flame_active) if self._last_boiler_state else False

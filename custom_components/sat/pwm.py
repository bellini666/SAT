from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass
from enum import IntEnum
from time import monotonic
from typing import TYPE_CHECKING

from . import control
from .const import HEATER_STARTUP_TIMEFRAME, MINIMUM_SETPOINT, BoilerStatus, PWMStatus
from .flame import Flame

if TYPE_CHECKING:
    from homeassistant.core import State

    from .boiler import BoilerState
    from .flame import FlameState
    from .heating_curve import HeatingCurve

_LOGGER = logging.getLogger(__name__)

_DURATIONS = struct.Struct("<2d")


class _Transition(IntEnum):
    START = 1
    STOP = 2
    HOURLY_LIMIT = 3
    CUT_SHORT = 4


SECONDS_PER_HOUR = 3600


@dataclass(frozen=True, slots=True, kw_only=True)
class CycleConfig:
    """Encapsulates settings related to cycle time and maximum cycles."""

    maximum_time: int
    maximum_count: int


@dataclass(frozen=True, slots=True, kw_only=True)
class PWMState:
    """Encapsulates the state of the PWM control."""

    enabled: bool
    status: PWMStatus
    duty_cycle: tuple[int, int] | None


class PWM:
    """Implements Pulse Width Modulation (PWM) control for managing boiler operations."""

    def __init__(
        self,
        cycles: CycleConfig,
        heating_curve: HeatingCurve,
        *,
        automatic_duty_cycle: bool,
        force: bool = False,
    ) -> None:
        """Initialize the PWM control."""
        self._alpha: float = 0.2
        self._force: bool = force
        self._last_boiler_temperature: float | None = None

        self._cycles: CycleConfig = cycles
        self._heating_curve: HeatingCurve = heating_curve
        self._automatic_duty_cycle: bool = automatic_duty_cycle

        # Initialize some helpers
        self._setpoint: float | None = None

        # The cycles per hour limit spans resets, otherwise every PID reset would start a new cycle
        self._current_cycle: int = 0
        self._first_duty_cycle_start: float | None = None

        self.reset()

    def reset(self) -> None:
        """Reset the PWM control."""
        self._enabled = False
        self._status: PWMStatus = PWMStatus.IDLE
        self._last_update: float = monotonic()
        self._duty_cycle: tuple[int, int] | None = None
        self._last_duty_cycle_percentage: float | None = None

    def restore(self, state: State) -> None:
        """Restore the PWM controller from a saved state."""
        if enabled := state.attributes.get("pulse_width_modulation_enabled"):
            self._enabled = bool(enabled)

    def enable(self) -> None:
        """Enable the PWM control."""
        self._enabled = True

    def disable(self) -> None:
        """Disable the PWM control."""
        self.reset()
        self._enabled = False
        self._setpoint = None

    async def update(
        self, boiler: BoilerState, flame: FlameState, requested_setpoint: float | None
    ) -> None:
        """Update the PWM state based on the output of a PID controller."""
        if (
            self._heating_curve.value is None
            or requested_setpoint is None
            or boiler.flow_temperature is None
        ):
            self._status = PWMStatus.IDLE
            self._last_update = monotonic()
            self._last_boiler_temperature = boiler.flow_temperature

            _LOGGER.debug(
                "PWM idle until the heating curve, requested setpoint and boiler temperature "
                "are known."
            )

            return

        if self._last_boiler_temperature is None:
            self._last_boiler_temperature = boiler.flow_temperature
            _LOGGER.debug("Initialized last boiler temperature to %.1f°C", boiler.flow_temperature)

        if (
            self._first_duty_cycle_start is None
            or (monotonic() - self._first_duty_cycle_start) > SECONDS_PER_HOUR
        ):
            self._current_cycle = 0
            self._first_duty_cycle_start = monotonic()
            _LOGGER.info("CYCLES count reset for the rolling hour.")

        elapsed = monotonic() - self._last_update
        self._duty_cycle = self._calculate_duty_cycle(
            requested_setpoint, self._last_boiler_temperature, boiler
        )

        self._last_boiler_temperature = control.value(
            "startup_flow",
            boiler.flow_temperature,
            self._last_boiler_temperature,
            self._status == PWMStatus.ON,
            bool(boiler.hot_water_active),
            elapsed,
            HEATER_STARTUP_TIMEFRAME,
            self._alpha,
        )
        adjusted = control.value(
            "pwm_setpoint",
            control.optional(self._setpoint),
            boiler.flow_temperature,
            flame.is_active,
            flame.is_inactive,
            boiler.status
            not in (
                BoilerStatus.PUMP_STARTING,
                BoilerStatus.BELOW_SETPOINT,
                BoilerStatus.OVERSHOOT_HANDLING,
            ),
            control.optional(flame.latest_on_time_seconds),
            control.optional(flame.average_on_time_seconds),
        )
        self._setpoint = None if math.isnan(adjusted) else adjusted

        if (
            # Check if we are above the overshoot temperature
            boiler.status == BoilerStatus.COOLING_DOWN
            and self._setpoint is not None
            and math.floor(requested_setpoint) > math.floor(self._setpoint)
        ):
            _LOGGER.info("Setpoint stabilization detected, disabling Pulse Width Modulation.")
            self.disable()
            return

        transition = control.value(
            "pwm_transition",
            self._status.control_code,
            elapsed,
            *self._duty_cycle,
            HEATER_STARTUP_TIMEFRAME,
            self._current_cycle >= self._cycles.maximum_count,
            sum(ended >= self._last_update for ended in flame.cut_short_burn_ends)
            >= Flame.PWM_SHORT_BURNS_THRESHOLD,
        )
        if transition == _Transition.START:
            self._current_cycle += 1
            self._status = PWMStatus.ON
            self._last_update = monotonic()
            if not boiler.hot_water_active:
                self._last_boiler_temperature = boiler.flow_temperature
        elif transition == _Transition.STOP:
            self._status = PWMStatus.OFF
            self._last_update = monotonic()
        elif transition == _Transition.HOURLY_LIMIT:
            self._status = PWMStatus.OFF
        elif transition == _Transition.CUT_SHORT:
            self._status = PWMStatus.OFF
            # Preserve the planned cycle length after two short burns.
            self._last_update += self._duty_cycle[0]

    def _calculate_duty_cycle(
        self, requested_setpoint: float, boiler_temperature: float, boiler: BoilerState
    ) -> tuple[int, int]:
        self._last_duty_cycle_percentage = control.value(
            "duty_fraction", requested_setpoint, boiler_temperature, self._heating_curve.base_offset
        )
        on, off = control.shared.record(
            _DURATIONS,
            "duty_cycle",
            self._last_duty_cycle_percentage,
            self._automatic_duty_cycle,
            bool(boiler.flame_active and not boiler.hot_water_active),
            self._cycles.maximum_time,
            self._cycles.maximum_count,
        )
        return int(on), int(off)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def status(self) -> PWMStatus:
        return self._status

    @property
    def state(self) -> PWMState:
        return PWMState(status=self._status, enabled=self._enabled, duty_cycle=self._duty_cycle)

    @property
    def duty_cycle(self) -> tuple[int, int] | None:
        """Current duty cycle as a tuple of (on_time, off_time) in seconds, or None if inactive."""
        return self._duty_cycle

    @property
    def last_duty_cycle_percentage(self) -> float | None:
        """Returns the last calculated duty cycle percentage."""
        return (
            round(self._last_duty_cycle_percentage * 100, 2)
            if self._last_duty_cycle_percentage is not None
            else None
        )

    @property
    def setpoint(self) -> float:
        """Returns the adjusted setpoint when running an ON duty cycle."""
        return self._setpoint or MINIMUM_SETPOINT

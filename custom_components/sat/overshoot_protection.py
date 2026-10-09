from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import fmean
from typing import Any, Mapping

from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import CONF_CALIBRATION_FLAME_TIMEOUT, CONF_CALIBRATION_PLATEAU_TIMEOUT, MINIMUM_RELATIVE_MODULATION, MINIMUM_SETPOINT, OPTIONS_DEFAULTS, OVERSHOOT_PROTECTION_SETPOINT
from .coordinator import DeviceState, SatDataUpdateCoordinator
from .helpers import convert_time_str_to_seconds

_LOGGER = logging.getLogger(__name__)

# The gateway drops a control setpoint override after about 60 seconds without a refresh
TICK = timedelta(seconds=30)

PLATEAU_WINDOW = timedelta(minutes=5)
PLATEAU_TOLERANCE = 0.5
MODULATION_TOLERANCE = 3
MODULATION_STABILITY = 5
SETPOINT_MARGIN = 2
MAXIMUM_FLAME_LOSSES = 3


class CalibrationError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    value: float
    method: str


def create_overshoot_protection(coordinator: SatDataUpdateCoordinator, heating_system: str, maximum_setpoint: float, options: Mapping[str, Any]) -> OvershootProtection:
    """Create the calibration with the timeouts from the options."""
    options = {**OPTIONS_DEFAULTS, **options}

    return OvershootProtection(
        coordinator,
        heating_system,
        maximum_setpoint,
        flame_timeout=timedelta(seconds=convert_time_str_to_seconds(options[CONF_CALIBRATION_FLAME_TIMEOUT])),
        plateau_timeout=timedelta(seconds=convert_time_str_to_seconds(options[CONF_CALIBRATION_PLATEAU_TIMEOUT])),
    )


class OvershootProtection:
    """Find the overshoot protection value by heating at a fixed setpoint until the flow temperature settles."""

    def __init__(
            self,
            coordinator: SatDataUpdateCoordinator,
            heating_system: str,
            maximum_setpoint: float,
            flame_timeout: timedelta = timedelta(seconds=convert_time_str_to_seconds(OPTIONS_DEFAULTS[CONF_CALIBRATION_FLAME_TIMEOUT])),
            plateau_timeout: timedelta = timedelta(seconds=convert_time_str_to_seconds(OPTIONS_DEFAULTS[CONF_CALIBRATION_PLATEAU_TIMEOUT])),
    ) -> None:
        self._coordinator = coordinator
        self._flame_timeout = flame_timeout
        self._plateau_timeout = plateau_timeout

        system_setpoint = OVERSHOOT_PROTECTION_SETPOINT[heating_system]
        self.setpoint = min(system_setpoint, maximum_setpoint)
        if self.setpoint < system_setpoint:
            _LOGGER.info("Calibrating at the maximum setpoint %.1f°C instead of %.1f°C for %s", self.setpoint, system_setpoint, heating_system)

        self.phase = "waiting_for_flame"
        self._samples: list[tuple[datetime, float, float]] = []
        self._waited = timedelta()
        self._heated = timedelta()
        self._flame_losses = 0
        self._last_tick = dt_util.utcnow()
        self._result: asyncio.Future[CalibrationResult] = coordinator.hass.loop.create_future()

    @property
    def progress(self) -> float:
        return min(self._heated / self._plateau_timeout, 1)

    async def calculate(self) -> CalibrationResult:
        """Run the calibration and always hand the boiler back afterwards."""
        self._last_tick = dt_util.utcnow()

        _LOGGER.info("Starting overshoot protection calibration at %.1f°C", self.setpoint)
        unsubscribe = async_track_time_interval(self._coordinator.hass, self._async_tick, TICK)

        try:
            await self._async_tick(self._last_tick)
            result = await self._result
            _LOGGER.info("Overshoot protection value %.1f°C, determined by %s", result.value, result.method)
            return result
        except CalibrationError as error:
            _LOGGER.warning("Overshoot protection calibration failed: %s", error.reason)
            raise
        finally:
            unsubscribe()
            await self._async_release()

    async def _async_release(self) -> None:
        _LOGGER.debug("Calibration: releasing the boiler overrides")
        await self._coordinator.async_set_heater_state(DeviceState.OFF)
        await self._coordinator.async_set_control_setpoint(MINIMUM_SETPOINT)
        await self._coordinator.async_release_control()

    async def _async_tick(self, now: datetime) -> None:
        if self._result.done():
            return

        try:
            if (result := await self._async_step(now)) is not None:
                self._result.set_result(result)
        except CalibrationError as error:
            self._result.set_exception(error)

    async def _async_step(self, now: datetime) -> CalibrationResult | None:
        elapsed = now - self._last_tick
        self._last_tick = now

        coordinator = self._coordinator
        await coordinator.async_set_heater_state(DeviceState.ON)
        await coordinator.async_set_control_setpoint(self.setpoint)
        await coordinator.async_set_control_max_relative_modulation(MINIMUM_RELATIVE_MODULATION)

        _LOGGER.debug(
            "Calibration %s: sent CH=on CS=%.1f MM=%d, flame=%s hot_water=%s flow=%s modulation=%s",
            self.phase, self.setpoint, MINIMUM_RELATIVE_MODULATION, coordinator.flame_active, coordinator.hot_water_active,
            coordinator.boiler_temperature, coordinator.relative_modulation_value,
        )

        if coordinator.hot_water_active:
            if self.phase != "paused":
                _LOGGER.info("Calibration paused for hot water")

            self.phase = "paused"
            self._samples.clear()
            return None

        if self.phase == "paused":
            _LOGGER.info("Calibration resumed after hot water")
            self.phase = "waiting_for_flame"

        if not coordinator.flame_active:
            if self.phase == "heating":
                self._flame_losses += 1
                self._samples.clear()
                _LOGGER.info("Calibration lost the flame (%d of %d allowed)", self._flame_losses, MAXIMUM_FLAME_LOSSES)

                if self._flame_losses > MAXIMUM_FLAME_LOSSES:
                    raise CalibrationError("flame_lost")

            self.phase = "waiting_for_flame"
            self._waited += elapsed
            if self._waited > self._flame_timeout:
                raise CalibrationError("no_flame")

            return None

        if self.phase == "waiting_for_flame":
            _LOGGER.info("Calibration flame on, heating at %.1f°C", self.setpoint)
            self.phase = "heating"
            self._waited = timedelta()

        self._heated += elapsed
        if coordinator.boiler_temperature is not None and coordinator.relative_modulation_value is not None:
            self._samples.append((now, float(coordinator.boiler_temperature), float(coordinator.relative_modulation_value)))

            if (result := self._plateau(now)) is not None:
                return result

        if self._heated > self._plateau_timeout:
            raise CalibrationError("timeout")

        return None

    def _plateau(self, now: datetime) -> CalibrationResult | None:
        if not self._samples or self._samples[0][0] > now - PLATEAU_WINDOW:
            return None

        window = [sample for sample in self._samples if sample[0] >= now - PLATEAU_WINDOW]
        flows = [flow for _, flow, _ in window]
        modulations = [modulation for _, _, modulation in window]
        floor = self._coordinator.minimum_relative_modulation_value or 0

        _LOGGER.debug(
            "Calibration window: flow %.1f-%.1f°C, modulation %.0f-%.0f%%, modulation floor %.0f%%",
            min(flows), max(flows), min(modulations), max(modulations), floor,
        )

        if max(flows) - min(flows) > PLATEAU_TOLERANCE:
            return None

        plateau = round(fmean(flows), 1)
        modulation = fmean(modulations)

        if modulation <= floor + MODULATION_TOLERANCE:
            if plateau >= self.setpoint - SETPOINT_MARGIN:
                raise CalibrationError("setpoint_reached")

            return CalibrationResult(plateau, "zero_modulation" if max(modulations) == 0 else "minimum_modulation")

        if max(modulations) - min(modulations) > MODULATION_STABILITY:
            return None

        return CalibrationResult(round((100 - modulation) / 100 * self.setpoint, 1), "formula")

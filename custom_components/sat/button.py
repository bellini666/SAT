from __future__ import annotations

import asyncio
import logging

from homeassistant.components import persistent_notification
from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SatConfigEntry
from .const import CONF_HEATING_SYSTEM, CONF_MAXIMUM_SETPOINT, CONF_MINIMUM_SETPOINT, CONF_OVERSHOOT_PROTECTION
from .entity import SatClimateEntity
from .helpers import calculate_default_maximum_setpoint
from .overshoot_protection import CalibrationError, create_overshoot_protection

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(_hass: HomeAssistant, config_entry: SatConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = config_entry.runtime_data.coordinator

    if coordinator.supports_setpoint_management:
        async_add_entities([SatCalibrateButton(coordinator, config_entry, config_entry.runtime_data.climate)])


class SatCalibrateButton(SatClimateEntity, ButtonEntity):
    _attr_translation_key = "calibrate_overshoot_protection"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, config_entry: SatConfigEntry, climate) -> None:
        super().__init__(coordinator, config_entry, climate)
        self._calibration: asyncio.Task | None = None

    @property
    def unique_id(self) -> str:
        return f"{self._config_entry.entry_id}-calibrate-overshoot-protection"

    @property
    def available(self) -> bool:
        return self._calibration is None

    async def async_press(self) -> None:
        if self._calibration is not None:
            return

        if not self._climate.valves_open:
            _LOGGER.warning("Calibrating while no valves report open, the measured value may come out too high")

        self._calibration = self._config_entry.async_create_background_task(self.hass, self._async_calibrate(), "sat_overshoot_protection_calibration")
        self.async_write_ha_state()

    async def _async_calibrate(self) -> None:
        entry = self._config_entry
        heating_system = entry.data.get(CONF_HEATING_SYSTEM)
        maximum_setpoint = float(entry.options.get(CONF_MAXIMUM_SETPOINT, calculate_default_maximum_setpoint(heating_system)))

        try:
            async with self._climate.async_pause_control():
                result = await create_overshoot_protection(self._coordinator, heating_system, maximum_setpoint, entry.options).calculate()
        except CalibrationError as error:
            persistent_notification.async_create(
                self.hass,
                f"Calibrating the overshoot protection value failed ({error.reason}). The current value stays in use.",
                title=entry.title,
                notification_id=f"sat_calibration_{entry.entry_id}",
            )
            self._calibration = None
            self.async_write_ha_state()
            return

        persistent_notification.async_create(
            self.hass,
            f"Overshoot protection value calibrated at {result.value} °C ({result.method}).",
            title=entry.title,
            notification_id=f"sat_calibration_{entry.entry_id}",
        )

        self.hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_OVERSHOOT_PROTECTION: True},
            options={**entry.options, CONF_MINIMUM_SETPOINT: result.value},
        )
        self.hass.config_entries.async_schedule_reload(entry.entry_id)

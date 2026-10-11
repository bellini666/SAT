from __future__ import annotations

import logging
from datetime import datetime, timedelta
from time import monotonic
from typing import TYPE_CHECKING, override

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.group.binary_sensor import BinarySensorGroup
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .climate import SatClimate
from .const import (
    CONF_MODE,
    CONF_WINDOW_SENSORS,
    MODE_MQTT_OPENTHERM,
    MODE_SERIAL,
    BoilerStatus,
    FlameStatus,
)
from .entity import SatClimateEntity, SatEntity
from .helpers import seconds_since
from .serial import binary_sensor as serial_binary_sensor

if TYPE_CHECKING:
    from .coordinator import SatDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__name__)


async def async_setup_entry(
    _hass: HomeAssistant, _config_entry: ConfigEntry, _async_add_entities: AddEntitiesCallback
):
    """Add binary sensors for the serial protocol if the integration is set to use it."""
    climate = _config_entry.runtime_data.climate
    coordinator = _config_entry.runtime_data.coordinator

    # Check if integration is set to use the serial protocol
    if _config_entry.data.get(CONF_MODE) == MODE_SERIAL:
        await serial_binary_sensor.async_setup_entry(_hass, _config_entry, _async_add_entities)

    if coordinator.supports_setpoint_management:
        _async_add_entities([SatControlSetpointSynchroSensor(coordinator, _config_entry, climate)])

    if coordinator.supports_relative_modulation_management:
        _async_add_entities(
            [SatRelativeModulationSynchroSensor(coordinator, _config_entry, climate)]
        )

    if _config_entry.data.get(CONF_MODE) == MODE_MQTT_OPENTHERM:
        _async_add_entities([SatBoilerFaultSensor(coordinator, _config_entry)])

    if len(_config_entry.options.get(CONF_WINDOW_SENSORS, [])) > 0:
        _async_add_entities([SatWindowSensor(coordinator, _config_entry, climate)])

    _async_add_entities(
        [
            SatFlameHealthSensor(coordinator, _config_entry),
            SatBoilerHealthSensor(coordinator, _config_entry),
            SatCentralHeatingSynchroSensor(coordinator, _config_entry, climate),
            SatHeatingControlSensor(coordinator, _config_entry, climate),
        ]
    )


class SatSynchroSensor:
    """Mixin to add delayed state change for binary sensors."""

    def __init__(self, delay: int = 60):
        """Initialize the mixin with a delay."""
        self._delay = delay
        self._last_mismatch = None

    def state_delayed(self, condition: bool) -> bool:
        """Determine the delayed state based on a condition."""
        # Outside SAT control the boiler follows the room thermostat, so a mismatch is expected
        if (
            not condition
            or self._climate.hvac_mode != HVACMode.HEAT
            or self._climate.control_paused
            or self._climate.setpoint is None
        ):
            self._last_mismatch = None
            return False

        if self._last_mismatch is None:
            self._last_mismatch = monotonic()

        if seconds_since(self._last_mismatch) >= self._delay:
            return True

        return False


class SatControlSetpointSynchroSensor(SatSynchroSensor, SatClimateEntity, BinarySensorEntity):
    _attr_translation_key = "control_setpoint_synchro"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, _config_entry, climate):
        SatSynchroSensor.__init__(self)
        SatClimateEntity.__init__(self, coordinator, _config_entry, climate)

        self._attr_unique_id = f"{_config_entry.entry_id}-control-setpoint-synchro"

    @override
    @property
    def available(self):
        """Return availability of the sensor."""
        return self._climate.setpoint is not None and self._coordinator.setpoint is not None

    @override
    def _update_attrs(self) -> None:
        if self.available:
            self._attr_is_on = self.state_delayed(
                round(self._climate.setpoint, 1) != round(self._coordinator.setpoint, 1)
            )


class SatRelativeModulationSynchroSensor(SatSynchroSensor, SatClimateEntity, BinarySensorEntity):
    _attr_translation_key = "relative_modulation_synchro"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, _config_entry, climate):
        SatSynchroSensor.__init__(self)
        SatClimateEntity.__init__(self, coordinator, _config_entry, climate)

        self._attr_unique_id = f"{_config_entry.entry_id}-relative-modulation-synchro"

    @override
    @property
    def available(self):
        """Return availability of the sensor."""
        return (
            self._climate.relative_modulation_value is not None
            and self._coordinator.maximum_relative_modulation_value is not None
        )

    @override
    def _update_attrs(self) -> None:
        if self.available:
            self._attr_is_on = self.state_delayed(
                int(self._climate.relative_modulation_value)
                != int(self._coordinator.maximum_relative_modulation_value)
            )


class SatCentralHeatingSynchroSensor(SatSynchroSensor, SatClimateEntity, BinarySensorEntity):
    _attr_translation_key = "central_heating_synchro"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, _config_entry, climate):
        SatSynchroSensor.__init__(self)
        SatClimateEntity.__init__(self, coordinator, _config_entry, climate)

        self._attr_unique_id = f"{_config_entry.entry_id}-central-heating-synchro"

    @override
    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self.climate_added

    @override
    def _update_attrs(self) -> None:
        if not self.available:
            return

        device_active = self._coordinator.device_active
        climate_hvac_action = self._climate.hvac_action

        self._attr_is_on = self.state_delayed(
            not (
                (climate_hvac_action == HVACAction.OFF and not device_active)
                or (climate_hvac_action == HVACAction.IDLE and not device_active)
                or (climate_hvac_action == HVACAction.HEATING and device_active)
            )
        )


class SatHeatingControlSensor(SatClimateEntity, BinarySensorEntity):
    _attr_translation_key = "heating_control"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry, climate: SatClimate
    ) -> None:
        super().__init__(coordinator, config_entry, climate)

        self._attr_unique_id = f"{config_entry.entry_id}-heating-control"

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_refresh, timedelta(seconds=30))
        )

    @callback
    def _async_refresh(self, _now: datetime) -> None:
        self._update_attrs()
        self.async_write_ha_state()

    @override
    @property
    def available(self) -> bool:
        return self.climate_added

    @override
    def _update_attrs(self) -> None:
        if not self.available:
            return

        problems = self._climate.control_problems
        self._attr_is_on = len(problems) > 0
        self._attr_extra_state_attributes = {"problems": problems}


class SatBoilerHealthSensor(SatEntity, BinarySensorEntity):
    _attr_translation_key = "boiler_health"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-boiler-health"

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_refresh, timedelta(seconds=30))
        )

    @callback
    def _async_refresh(self, _now: datetime) -> None:
        self._update_attrs()
        self.async_write_ha_state()

    @override
    def _update_attrs(self) -> None:
        self._attr_is_on = self._coordinator.fault_active or (
            self._coordinator.boiler.status == BoilerStatus.INSUFFICIENT_DATA
            and not self._coordinator.awaiting_boiler_data
        )


class SatFlameHealthSensor(SatEntity, BinarySensorEntity):
    _attr_translation_key = "flame_health"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-flame-health"

    @override
    @property
    def available(self) -> bool:
        """Return availability of the sensor."""
        return self._coordinator.flame.health_status != FlameStatus.INSUFFICIENT_DATA

    @override
    def _update_attrs(self) -> None:
        self._attr_is_on = self._coordinator.flame.health_status not in (
            FlameStatus.HEALTHY,
            FlameStatus.IDLE_OK,
        )


class SatWindowSensor(SatClimateEntity, BinarySensorGroup):
    _attr_translation_key = "window"

    def __init__(self, coordinator, config_entry: ConfigEntry, climate: SatClimate):
        super().__init__(coordinator, config_entry, climate)

        self.mode = any
        self._entity_ids = self._config_entry.options.get(CONF_WINDOW_SENSORS)
        self._attr_unique_id = f"{config_entry.entry_id}-window-sensor"
        self._device_class = BinarySensorDeviceClass.WINDOW
        self._attr_extra_state_attributes = {ATTR_ENTITY_ID: self._entity_ids}

    @override
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.entity_id], self._climate.async_window_sensor_changed
            )
        )


class SatBoilerFaultSensor(SatEntity, BinarySensorEntity):
    _attr_translation_key = "boiler_fault"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SatDataUpdateCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator, config_entry)

        self._attr_unique_id = f"{config_entry.entry_id}-boiler-fault"

    @override
    def _update_attrs(self) -> None:
        self._attr_is_on = self._coordinator.fault_active
        self._attr_extra_state_attributes = {"flags": self._coordinator.fault_flags}

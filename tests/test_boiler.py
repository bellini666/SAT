"""Tests for the boiler temperature derivative and tracking."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.boiler import BoilerTemperatureTracker
from custom_components.sat.const import CONF_HEATING_CURVE_COEFFICIENT, DOMAIN, BoilerStatus
from custom_components.sat.coordinator import DeviceState
from tests.const import DEFAULT_USER_DATA


async def test_boiler_temperature_derivative_keeps_slow_changes(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set("sensor.test_inside_sensor", "20.9")
    hass.states.async_set("sensor.test_outside_sensor", "9.9")

    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA, options={CONF_HEATING_CURVE_COEFFICIENT: 1.8})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    await coordinator.async_set_boiler_temperature(50.0)
    await coordinator.async_control_heating_loop()

    freezer.tick(timedelta(seconds=30))
    await coordinator.async_set_boiler_temperature(50.1)
    await coordinator.async_control_heating_loop()

    assert abs(coordinator.boiler_temperature_derivative - 0.1 / 30) < 1e-6
    assert entry.runtime_data.climate.extra_state_attributes["boiler_temperature_derivative"] == 0.003
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_slow_rise_below_the_cold_temperature_is_pump_starting(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.states.async_set("sensor.test_inside_sensor", "20.9")
    hass.states.async_set("sensor.test_outside_sensor", "9.9")

    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA, options={CONF_HEATING_CURVE_COEFFICIENT: 1.8})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    await coordinator.async_set_boiler_temperature(45.0)
    await coordinator.async_control_heating_loop()

    freezer.tick(timedelta(seconds=30))
    await coordinator.async_set_control_setpoint(55.0)
    await coordinator.async_set_heater_state(DeviceState.ON)
    await coordinator.async_set_boiler_temperature(42.0)
    await coordinator.async_control_heating_loop()

    freezer.tick(timedelta(seconds=30))
    await coordinator.async_set_boiler_temperature(42.1)
    await coordinator.async_control_heating_loop()

    assert coordinator.boiler_temperature_cold == 45.0
    assert coordinator.device_status == BoilerStatus.PUMP_STARTING
    assert await hass.config_entries.async_unload(entry.entry_id)


def test_tracker_treats_a_slow_drift_as_stable() -> None:
    tracker = BoilerTemperatureTracker()
    tracker.update(boiler_temperature=40, boiler_temperature_derivative=0.0, flame_active=False, setpoint=50)
    tracker.update(boiler_temperature=48, boiler_temperature_derivative=0.05, flame_active=True, setpoint=50)
    tracker.update(boiler_temperature=46, boiler_temperature_derivative=-0.01, flame_active=True, setpoint=50)
    assert tracker.active

    tracker.update(boiler_temperature=46, boiler_temperature_derivative=0.003, flame_active=True, setpoint=50)

    assert tracker.inactive

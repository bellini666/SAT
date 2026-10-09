"""Tests for the overshoot protection calibration button."""

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import persistent_notification
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import DOMAIN
from tests.const import DEFAULT_USER_DATA

BUTTON = "button.mock_title_calibrate_overshoot_protection"


async def setup_heating_entry(hass: HomeAssistant) -> MockConfigEntry:
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")

    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "heating_system": "radiators"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await entry.runtime_data.climate.async_set_hvac_mode(HVACMode.HEAT)

    return entry


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, count: int) -> None:
    for _ in range(count):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_button_calibrates_and_stores_the_value(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating_entry(hass)
    await entry.runtime_data.coordinator.async_set_boiler_temperature(40)

    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    assert hass.states.get(BUTTON).state == STATE_UNAVAILABLE
    assert entry.runtime_data.climate.control_paused

    await tick(hass, freezer, 14)

    assert entry.options["minimum_setpoint"] == 40.0
    assert entry.runtime_data.coordinator.minimum_setpoint == 40.0
    assert entry.runtime_data.climate.hvac_mode == HVACMode.HEAT
    assert not entry.runtime_data.climate.control_paused
    assert hass.states.get(BUTTON).state != STATE_UNAVAILABLE
    notification = persistent_notification._async_get_or_create_notifications(hass)[f"sat_calibration_{entry.entry_id}"]
    assert "40.0" in notification["message"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unload_cancels_a_running_calibration(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating_entry(hass)

    coordinator = entry.runtime_data.coordinator
    will_remove_from_hass = coordinator.async_will_remove_from_hass
    calls = []

    async def release_control() -> None:
        calls.append("release")

    async def remove_from_hass() -> None:
        calls.append("removed")
        await will_remove_from_hass()

    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    await tick(hass, freezer, 2)

    with patch.object(coordinator, "async_release_control", release_control), patch.object(coordinator, "async_will_remove_from_hass", remove_from_hass):
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert calls[-1] == "removed"
    assert entry.options.get("minimum_setpoint") is None


async def test_calibration_timeouts_come_from_the_options(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating_entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "calibration_plateau_timeout": "00:05:00"})
    coordinator = entry.runtime_data.coordinator

    await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
    for step in range(12):
        await coordinator.async_set_boiler_temperature(30 + step)
        await tick(hass, freezer, 1)

    notification = persistent_notification._async_get_or_create_notifications(hass)[f"sat_calibration_{entry.entry_id}"]
    assert "timeout" in notification["message"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_gateway_error_ends_the_calibration(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating_entry(hass)
    coordinator = entry.runtime_data.coordinator

    with patch.object(coordinator, "async_set_control_max_relative_modulation", side_effect=RuntimeError("gateway")):
        await hass.services.async_call("button", "press", {"entity_id": BUTTON}, blocking=True)
        await hass.async_block_till_done()

    assert hass.states.get(BUTTON).state != STATE_UNAVAILABLE
    assert not entry.runtime_data.climate.control_paused
    notification = persistent_notification._async_get_or_create_notifications(hass)[f"sat_calibration_{entry.entry_id}"]
    assert "gateway" in notification["message"]
    assert await hass.config_entries.async_unload(entry.entry_id)

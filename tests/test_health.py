"""Tests for the SAT health binary sensor and repair issues."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.climate import HVACMode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.sat.const import CONF_DEVICE, CONF_MODE, CONF_MQTT_TOPIC, DOMAIN, MODE_MQTT_OPENTHERM
from tests.const import DEFAULT_USER_DATA

ENTITY_ID = "binary_sensor.mock_title_heating_control"


async def setup_heating(hass: HomeAssistant) -> MockConfigEntry:
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")

    entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    climate = entry.runtime_data.climate
    await entry.runtime_data.coordinator.async_set_boiler_temperature(40)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()
    await hass.async_block_till_done()

    return entry


async def test_healthy_while_controlling(hass: HomeAssistant) -> None:
    entry = await setup_heating(hass)

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_OFF
    assert state.attributes["problems"] == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_flags_a_stalled_control_loop(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating(hass)

    freezer.tick(timedelta(minutes=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == STATE_ON
    assert state.attributes["problems"] == ["control_loop_stalled"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_flags_missing_inputs_and_recovers(hass: HomeAssistant) -> None:
    entry = await setup_heating(hass)

    hass.states.async_set("sensor.test_inside_sensor", "unavailable")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).attributes["problems"] == ["inputs_missing"]
    assert hass.states.get(ENTITY_ID).state == STATE_ON

    hass.states.async_set("sensor.test_inside_sensor", "19.6")
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY_ID).state == STATE_OFF
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_flags_missing_boiler_data(hass: HomeAssistant) -> None:
    entry = await setup_heating(hass)

    await entry.runtime_data.coordinator.async_set_boiler_temperature(None)
    await entry.runtime_data.climate.async_control_heating_loop()
    await hass.async_block_till_done()

    assert hass.states.get(ENTITY_ID).attributes["problems"] == ["boiler_data_missing"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_setup_raises_a_repair_issue(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={**DEFAULT_USER_DATA, CONF_MODE: MODE_MQTT_OPENTHERM, CONF_DEVICE: "otgw", CONF_MQTT_TOPIC: "OTGW"},
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"setup_failed_{entry.entry_id}")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR


async def test_successful_setup_clears_the_repair_issue(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)
    ir.async_create_issue(hass, DOMAIN, f"setup_failed_{entry.entry_id}", is_fixable=False, severity=ir.IssueSeverity.ERROR, translation_key="setup_failed")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"setup_failed_{entry.entry_id}") is None
    assert await hass.config_entries.async_unload(entry.entry_id)

"""Tests for the SAT health binary sensor and repair issues."""

from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.sat.const import (
    CONF_DEVICE,
    CONF_MODE,
    CONF_MQTT_TOPIC,
    DOMAIN,
    MODE_MQTT_OPENTHERM,
)
from custom_components.sat.coordinator import DeviceState
from tests.const import DEFAULT_USER_DATA

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory

ENTITY_ID = "binary_sensor.mock_title_heating_control"
BOILER_HEALTH = "binary_sensor.mock_title_boiler_health"


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
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes["problems"] == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_flags_a_stalled_control_loop(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_heating(hass)

    freezer.tick(timedelta(minutes=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["problems"] == ["control_loop_stalled"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_healthy_while_paused(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await setup_heating(hass)

    async with entry.runtime_data.climate.async_calibrating():
        freezer.tick(timedelta(minutes=3))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        state = hass.states.get(ENTITY_ID)
        assert state is not None
        assert state.attributes["problems"] == []

    assert entry.runtime_data.climate.control_problems == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_healthy_right_after_turning_on(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_heating(hass)
    climate = entry.runtime_data.climate

    await climate.async_set_hvac_mode(HVACMode.OFF)
    freezer.tick(timedelta(minutes=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    await climate.async_set_hvac_mode(HVACMode.HEAT)

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["problems"] == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_healthy_while_home_assistant_starts(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.set_state(CoreState.starting)
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")
    entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await entry.runtime_data.coordinator.async_set_boiler_temperature(40)

    freezer.tick(timedelta(minutes=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["problems"] == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_flags_missing_inputs_and_recovers(hass: HomeAssistant) -> None:
    entry = await setup_heating(hass)

    hass.states.async_set("sensor.test_inside_sensor", "unavailable")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["problems"] == ["inputs_missing"]
    assert state.state == STATE_ON

    hass.states.async_set("sensor.test_inside_sensor", "19.6")
    await hass.async_block_till_done()
    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_flags_missing_boiler_data(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_heating(hass)
    freezer.tick(timedelta(minutes=3))

    await entry.runtime_data.coordinator.async_set_boiler_temperature(None)
    await entry.runtime_data.climate.async_control_heating_loop()
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["problems"] == ["boiler_data_missing"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_healthy_while_the_first_boiler_data_arrives(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")
    entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    climate = entry.runtime_data.climate
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == STATE_OFF
    state = hass.states.get(BOILER_HEALTH)
    assert state is not None
    assert state.state == STATE_OFF

    freezer.tick(timedelta(minutes=3))
    await climate.async_control_heating_loop()
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.attributes["problems"] == ["boiler_data_missing"]
    state = hass.states.get(BOILER_HEALTH)
    assert state is not None
    assert state.state == STATE_ON
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_setup_raises_a_repair_issue_after_retrying(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={
            **DEFAULT_USER_DATA,
            CONF_MODE: MODE_MQTT_OPENTHERM,
            CONF_DEVICE: "otgw",
            CONF_MQTT_TOPIC: "OTGW",
        },
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"setup_failed_{entry.entry_id}") is None

    for _ in range(6):
        freezer.tick(timedelta(seconds=90))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"setup_failed_{entry.entry_id}")
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR


async def test_successful_setup_clears_the_repair_issue(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"setup_failed_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="setup_failed",
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"setup_failed_{entry.entry_id}") is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_synchro_sensors_ignore_the_room_thermostat_while_off(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_heating(hass)
    climate = entry.runtime_data.climate
    coordinator = entry.runtime_data.coordinator

    await climate.async_set_hvac_mode(HVACMode.OFF)
    await coordinator.async_set_control_setpoint(30)
    await coordinator.async_set_heater_state(DeviceState.ON)
    climate.async_write_ha_state()
    freezer.tick(timedelta(seconds=61))
    climate.async_write_ha_state()

    state = hass.states.get("binary_sensor.mock_title_control_setpoint_synchro")
    assert state is not None
    assert state.state == STATE_OFF
    state = hass.states.get("binary_sensor.mock_title_central_heating_synchro")
    assert state is not None
    assert state.state == STATE_OFF
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_central_heating_synchro_follows_the_boiler_between_climate_writes(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_heating(hass)
    climate = entry.runtime_data.climate
    coordinator = entry.runtime_data.coordinator

    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await coordinator.async_set_heater_state(DeviceState.OFF)
    await climate.async_control_heating_loop()
    assert climate.setpoint is not None
    await coordinator.async_set_heater_state(DeviceState.OFF)
    climate.async_write_ha_state()
    assert climate.hvac_action == HVACAction.IDLE

    await coordinator.async_set_heater_state(DeviceState.ON)
    coordinator.async_update_listeners()
    freezer.tick(timedelta(seconds=61))
    coordinator.async_update_listeners()

    state = hass.states.get("binary_sensor.mock_title_central_heating_synchro")
    assert state is not None
    assert state.state == STATE_OFF
    assert await hass.config_entries.async_unload(entry.entry_id)

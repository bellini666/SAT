"""Tests for config entry setup, reload and unload."""

import asyncio
import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.config_entries import SOURCE_DHCP
from homeassistant.core import HomeAssistant
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_mqtt_message, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import MqttMockHAClient

from custom_components import sat
from custom_components.sat.const import CONF_DEVICE, CONF_MODE, CONF_MQTT_TOPIC, DOMAIN, MODE_MQTT_OPENTHERM
from tests.const import DEFAULT_USER_DATA


@pytest.fixture
async def sat_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA, unique_id="fake")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return entry


def errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


async def test_data_update_with_core_reload_reloads_once(hass: HomeAssistant, sat_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture) -> None:
    caplog.clear()

    hass.config_entries.async_update_entry(sat_entry, data={**sat_entry.data, "device": "changed"})
    hass.config_entries.async_schedule_reload(sat_entry.entry_id)
    await hass.async_block_till_done()

    assert sat_entry.state is ConfigEntryState.LOADED
    assert errors(caplog) == []
    assert len(hass.states.async_entity_ids("climate")) == 1


async def test_concurrent_reloads(hass: HomeAssistant, sat_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture) -> None:
    caplog.clear()

    await asyncio.gather(
        hass.config_entries.async_reload(sat_entry.entry_id),
        hass.config_entries.async_reload(sat_entry.entry_id),
    )
    await hass.async_block_till_done()

    assert sat_entry.state is ConfigEntryState.LOADED
    assert errors(caplog) == []
    assert len(hass.states.async_entity_ids("climate")) == 1


async def test_options_flow_reloads_entry(hass: HomeAssistant, sat_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture) -> None:
    caplog.clear()

    result = await hass.config_entries.options.async_init(sat_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "presets"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "activity_temperature": 10,
        "away_temperature": 10,
        "sleep_temperature": 15,
        "home_temperature": 18,
        "comfort_temperature": 20,
        "sync_climates_with_preset": False,
        "push_setpoint_to_thermostat": False,
    })
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert sat_entry.options["home_temperature"] == 18
    assert sat_entry.state is ConfigEntryState.LOADED
    assert errors(caplog) == []
    assert len(hass.states.async_entity_ids("climate")) == 1


async def test_unload(hass: HomeAssistant, sat_entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(sat_entry.entry_id)
    await hass.async_block_till_done()

    assert sat_entry.state is ConfigEntryState.NOT_LOADED
    assert all(state.state == "unavailable" for state in hass.states.async_all("climate"))


async def test_setup_does_not_depend_on_platform_order(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(sat, "PLATFORMS", [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.NUMBER, Platform.CLIMATE])

    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA, unique_id="fake")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert errors(caplog) == []
    assert len(hass.states.async_entity_ids("climate")) == 1
    assert hass.states.get("sensor.mock_title_heating_curve") is not None
    assert hass.states.get("binary_sensor.mock_title_central_heating_synchro") is not None


def mqtt_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="otgw",
        data={**DEFAULT_USER_DATA, CONF_MODE: MODE_MQTT_OPENTHERM, CONF_DEVICE: "otgw", CONF_MQTT_TOPIC: "OTGW"},
    )
    entry.add_to_hass(hass)

    return entry


async def test_unload_stops_mqtt_updates(hass: HomeAssistant, mqtt_mock: MqttMockHAClient) -> None:
    entry = mqtt_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinator
    async_fire_mqtt_message(hass, "OTGW/value/otgw/flame", "ON")
    await hass.async_block_till_done()
    assert coordinator.flame_active

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    async_fire_mqtt_message(hass, "OTGW/value/otgw/flame", "OFF")
    await hass.async_block_till_done()
    assert coordinator.flame_active


async def test_setup_retries_while_mqtt_is_unavailable(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry = mqtt_entry(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.states.async_entity_ids("climate") == []
    assert errors(caplog) == []


async def test_dhcp_discovery_keeps_mqtt_entry(hass: HomeAssistant, mqtt_mock: MqttMockHAClient, caplog: pytest.LogCaptureFixture) -> None:
    entry = mqtt_entry(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    caplog.clear()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(ip="192.168.1.10", hostname="otgw", macaddress="aabbccddeeff"),
    )
    await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert entry.data[CONF_DEVICE] == "otgw"
    assert entry.state is ConfigEntryState.LOADED
    assert "update listener" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("entity_id", ["climate.mock_title", ["climate.mock_title"]])
async def test_reset_integral_targets_reloaded_climate(hass: HomeAssistant, sat_entry: MockConfigEntry, entity_id: str | list[str]) -> None:
    assert await hass.config_entries.async_reload(sat_entry.entry_id)
    await hass.async_block_till_done()

    climate = sat_entry.runtime_data.climate
    assert climate.entity_id == "climate.mock_title"

    with patch.object(climate.pid, "reset") as reset:
        await hass.services.async_call(DOMAIN, "reset_integral", {"entity_id": entity_id}, blocking=True)

    reset.assert_called_once()


async def test_direct_control_loop_cancels_the_scheduled_run(hass: HomeAssistant, sat_entry: MockConfigEntry) -> None:
    climate = sat_entry.runtime_data.climate

    climate.schedule_control_heating_loop()
    await climate.async_control_heating_loop()

    assert await hass.config_entries.async_unload(sat_entry.entry_id)


async def test_periodic_tick_schedules_the_control_loop(hass: HomeAssistant, sat_entry: MockConfigEntry) -> None:
    climate = sat_entry.runtime_data.climate

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()

    assert climate._control_heating_loop_unsub is not None
    assert await hass.config_entries.async_unload(sat_entry.entry_id)

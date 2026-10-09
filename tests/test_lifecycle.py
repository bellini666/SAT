"""Tests for config entry setup, reload and unload."""

import asyncio
import logging

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.const import DOMAIN
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

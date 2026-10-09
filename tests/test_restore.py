"""Tests for restoring the climate state."""

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_restore_cache_with_extra_data

from custom_components.sat.const import DOMAIN
from tests.const import DEFAULT_USER_DATA

ENTITY_ID = "climate.mock_title"


async def setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return entry


async def test_restores_extra_data_when_last_state_is_unavailable(hass: HomeAssistant) -> None:
    mock_restore_cache_with_extra_data(hass, [(
        State(ENTITY_ID, STATE_UNAVAILABLE, {"restored": True}),
        {"hvac_mode": HVACMode.HEAT, "target_temperature": 21.0, "preset_mode": "home"},
    )])

    await setup_entry(hass)

    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.HEAT
    assert state.attributes["temperature"] == 21.0
    assert state.attributes["preset_mode"] == "home"


async def test_restores_last_state_without_extra_data(hass: HomeAssistant) -> None:
    mock_restore_cache_with_extra_data(hass, [(
        State(ENTITY_ID, HVACMode.HEAT, {"temperature": 20.5, "preset_mode": "none"}),
        {},
    )])

    await setup_entry(hass)

    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.HEAT
    assert state.attributes["temperature"] == 20.5


async def test_reload_keeps_mode_and_target(hass: HomeAssistant) -> None:
    entry = await setup_entry(hass)
    await hass.services.async_call("climate", "set_temperature", {"entity_id": ENTITY_ID, "temperature": 21.5}, blocking=True)
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": ENTITY_ID, "hvac_mode": HVACMode.HEAT}, blocking=True)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == HVACMode.HEAT
    assert state.attributes["temperature"] == 21.5

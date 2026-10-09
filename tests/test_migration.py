"""Tests for config entry migrations."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import DOMAIN
from tests.const import DEFAULT_USER_DATA


async def test_v10_copies_sync_with_thermostat(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=10, data={**DEFAULT_USER_DATA, "sync_with_thermostat": True})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == SatFlowHandler.VERSION
    assert entry.data["push_setpoint_to_thermostat"] is True

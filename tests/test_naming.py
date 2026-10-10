"""Tests for entity and device naming."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.const import DOMAIN
from tests.const import DEFAULT_USER_DATA


async def test_entities_are_named_after_the_device(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, title="Living Room", data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert device.name == "Living Room"
    assert device.suggested_area is None

    assert hass.states.get("climate.living_room").attributes["friendly_name"] == "Living Room"
    assert hass.states.get("sensor.living_room_heating_curve").attributes["friendly_name"] == "Living Room Heating curve"
    assert hass.states.get("sensor.living_room_boiler_status").attributes["friendly_name"] == "Living Room Boiler status"
    assert hass.states.get("binary_sensor.living_room_central_heating_synchro").attributes["friendly_name"] == "Living Room Central heating synchro"

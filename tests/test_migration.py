"""Tests for config entry migrations."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import CONF_NAME, DOMAIN
from tests.const import DEFAULT_USER_DATA


async def test_v10_copies_sync_with_thermostat(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=10, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "sync_with_thermostat": True})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == SatFlowHandler.VERSION
    assert entry.options["push_setpoint_to_thermostat"] is True


async def test_v11_moves_unique_ids_to_entry_id(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=11, data={**DEFAULT_USER_DATA, "name": "Living Room", "minimum_setpoint": 45})
    entry.add_to_hass(hass)

    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, "Living Room")})
    entities = er.async_get(hass)
    old = {
        ("climate", "living room"): "climate.living_room_thermostat",
        ("sensor", "living room-heating-curve"): "sensor.living_room_heating_curve",
        ("binary_sensor", "living room-central-heating-synchro"): "binary_sensor.living_room_central_heating_synchro",
        ("sensor", "Living Room-otgw-key"): "sensor.living_room_otgw_key",
    }
    for (domain, unique_id), entity_id in old.items():
        entities.async_get_or_create(domain, DOMAIN, unique_id, config_entry=entry, device_id=device.id, suggested_object_id=entity_id.split(".")[1])

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entities.async_get("climate.living_room_thermostat").unique_id == entry.entry_id
    assert entities.async_get("sensor.living_room_heating_curve").unique_id == f"{entry.entry_id}-heating-curve"
    assert entities.async_get("binary_sensor.living_room_central_heating_synchro").unique_id == f"{entry.entry_id}-central-heating-synchro"
    assert entities.async_get("sensor.living_room_otgw_key").unique_id == f"{entry.entry_id}-otgw-key"
    assert len(hass.states.async_entity_ids("climate")) == 1
    assert hass.states.get("climate.living_room_thermostat") is not None
    assert dr.async_get(hass).async_get(device.id).identifiers == {(DOMAIN, entry.entry_id)}


async def test_entries_with_the_same_name_do_not_collide(hass: HomeAssistant) -> None:
    for _ in range(2):
        entry = MockConfigEntry(domain=DOMAIN, data={**DEFAULT_USER_DATA, "name": "Living Room", "minimum_setpoint": 45})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert len(hass.states.async_entity_ids("climate")) == 2


async def test_v11_moves_push_setpoint_to_options(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=11, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "push_setpoint_to_thermostat": True})
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.options["push_setpoint_to_thermostat"] is True


async def test_v11_without_a_name_migrates(hass: HomeAssistant) -> None:
    data = {key: value for key, value in DEFAULT_USER_DATA.items() if key != CONF_NAME}
    entry = MockConfigEntry(domain=DOMAIN, version=11, data={**data, "minimum_setpoint": 45})
    entry.add_to_hass(hass)
    entities = er.async_get(hass)
    entities.async_get_or_create("climate", DOMAIN, "none", config_entry=entry, suggested_object_id="sat")

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == SatFlowHandler.VERSION
    assert entities.async_get("climate.sat").unique_id == entry.entry_id

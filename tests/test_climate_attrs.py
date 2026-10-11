"""Tests for the climate attributes that follow other entities."""

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_PRESET_MODE,
    PRESET_AWAY,
    PRESET_NONE,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, STATE_UNAVAILABLE
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.sat.const import (
    CONF_HUMIDITY_SENSOR_ENTITY_ID,
    CONF_SENSOR_MAX_VALUE_AGE,
    CONF_THERMAL_COMFORT,
    DOMAIN,
)
from custom_components.sat.summer_simmer import SummerSimmer
from tests.const import DEFAULT_USER_DATA

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

INSIDE = "sensor.test_inside_sensor"
HUMIDITY = "sensor.test_humidity_sensor"


async def setup_climate(
    hass: HomeAssistant, data: dict[str, Any] | None = None, options: dict[str, Any] | None = None
) -> MockConfigEntry:
    hass.states.async_set(INSIDE, "20.9")
    hass.states.async_set("sensor.test_outside_sensor", "9.9")
    hass.states.async_set(HUMIDITY, "50")

    entry = MockConfigEntry(
        domain=DOMAIN, data={**DEFAULT_USER_DATA, **(data or {})}, options=options or {}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    return entry


async def test_inside_sensor_change_updates_current_temperature(hass: HomeAssistant) -> None:
    entry = await setup_climate(hass)
    climate = entry.runtime_data.climate
    assert climate.current_temperature == 20.9

    hass.states.async_set(INSIDE, "21.4")
    await hass.async_block_till_done()

    assert climate.current_temperature == 21.4
    state = hass.states.get(climate.entity_id)
    assert state is not None
    assert state.attributes["current_temperature"] == 21.4
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unavailable_inside_sensor_clears_current_temperature(hass: HomeAssistant) -> None:
    entry = await setup_climate(hass)
    climate = entry.runtime_data.climate

    hass.states.async_set(INSIDE, STATE_UNAVAILABLE)
    await hass.async_block_till_done()

    assert climate.current_temperature is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_humidity_change_updates_thermal_comfort_temperature(hass: HomeAssistant) -> None:
    entry = await setup_climate(
        hass, {CONF_HUMIDITY_SENSOR_ENTITY_ID: HUMIDITY}, {CONF_THERMAL_COMFORT: True}
    )
    climate = entry.runtime_data.climate
    assert climate.current_humidity == 50.0
    assert climate.current_temperature == SummerSimmer.index(20.9, 50.0)

    hass.states.async_set(HUMIDITY, "80")
    await hass.async_block_till_done()

    assert climate.current_humidity == 80.0
    assert climate.current_temperature == SummerSimmer.index(20.9, 80.0)
    state = hass.states.get(climate.entity_id)
    assert state is not None
    assert state.attributes["current_humidity"] == 80.0
    assert state.attributes["summer_simmer_index"] == SummerSimmer.index(
        climate.current_temperature, 80.0
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_hvac_mode_change_publishes_hvac_action(hass: HomeAssistant) -> None:
    entry = await setup_climate(hass)
    climate = entry.runtime_data.climate

    await climate.async_set_hvac_mode(HVACMode.OFF)
    await hass.async_block_till_done()
    state = hass.states.get(climate.entity_id)
    assert state is not None
    assert state.state == HVACMode.OFF
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.OFF

    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await hass.async_block_till_done()
    state = hass.states.get(climate.entity_id)
    assert state is not None
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_TEMPERATURE] == 21.0
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_stale_inside_sensor_clears_current_temperature(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_climate(hass, options={CONF_SENSOR_MAX_VALUE_AGE: "00:10:00"})
    climate = entry.runtime_data.climate
    assert climate.current_temperature == 20.9

    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert climate.current_temperature is None
    state = hass.states.get(climate.entity_id)
    assert state is not None
    assert state.attributes["current_temperature"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_control_loop_before_the_stale_publish_keeps_it(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await setup_climate(hass, options={CONF_SENSOR_MAX_VALUE_AGE: "00:10:00"})
    climate = entry.runtime_data.climate
    await climate.async_set_hvac_mode(HVACMode.OFF)

    freezer.tick(timedelta(seconds=600.5))
    await climate.async_control_heating_loop()
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(climate.entity_id)
    assert state is not None
    assert state.attributes["current_temperature"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_state_write_after_unload_schedules_no_stale_publish(hass: HomeAssistant) -> None:
    entry = await setup_climate(hass, options={CONF_SENSOR_MAX_VALUE_AGE: "00:10:00"})
    climate = entry.runtime_data.climate
    assert await hass.config_entries.async_unload(entry.entry_id)

    climate.async_write_ha_state()

    assert climate._sensor_stale_unsub is None


async def test_clearing_a_restored_preset_publishes_it(hass: HomeAssistant) -> None:
    entry = await setup_climate(hass)
    climate = entry.runtime_data.climate
    climate._attr_preset_mode = PRESET_AWAY
    climate._pre_custom_temperature = None
    climate.async_write_ha_state()

    await climate.async_set_preset_mode(PRESET_NONE)

    state = hass.states.get(climate.entity_id)
    assert state is not None
    assert state.attributes[ATTR_PRESET_MODE] == PRESET_NONE
    assert await hass.config_entries.async_unload(entry.entry_id)

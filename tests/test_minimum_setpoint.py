"""Tests for the dynamic minimum setpoint."""

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.boiler import BoilerState
from custom_components.sat.const import DOMAIN, BoilerStatus, PWMStatus
from custom_components.sat.minimum_setpoint import MinimumSetpoint
from tests.const import DEFAULT_USER_DATA

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant


def boiler_state(**overrides: float | None) -> BoilerState:
    values = {
        "is_active": True,
        "is_inactive": False,
        "status": BoilerStatus.AT_SETPOINT,
        "flame_active": True,
        "hot_water_active": False,
        "setpoint": 50.0,
        "flow_temperature": 50.0,
        "return_temperature": 40.0,
        "relative_modulation_level": 30.0,
    }
    values.update(overrides)
    return BoilerState(**values)


def test_missing_sensor_values_keep_the_last_minimum_setpoint() -> None:
    minimum_setpoint = MinimumSetpoint(adjustment_factor=0.2, configured_minimum_setpoint=40.0)
    minimum_setpoint.warming_up(boiler_state())
    minimum_setpoint.calculate(boiler_state(return_temperature=45.0), PWMStatus.IDLE)

    minimum_setpoint.warming_up(boiler_state(return_temperature=None))
    minimum_setpoint.calculate(boiler_state(relative_modulation_level=None), PWMStatus.IDLE)
    minimum_setpoint.calculate(boiler_state(return_temperature=None), PWMStatus.IDLE)
    minimum_setpoint.calculate(
        boiler_state(flow_temperature=None, return_temperature=45.0), PWMStatus.IDLE
    )

    assert minimum_setpoint.current == 41.0


def test_missing_values_while_modulation_is_steady_keep_the_last_minimum_setpoint(
    freezer: FrozenDateTimeFactory,
) -> None:
    minimum_setpoint = MinimumSetpoint(adjustment_factor=0.2, configured_minimum_setpoint=40.0)
    minimum_setpoint.warming_up(boiler_state())
    minimum_setpoint.calculate(boiler_state(), PWMStatus.IDLE)
    freezer.tick(timedelta(seconds=181))
    minimum_setpoint.calculate(boiler_state(), PWMStatus.IDLE)
    assert minimum_setpoint.current == 32.0

    minimum_setpoint.calculate(
        boiler_state(relative_modulation_level=None, return_temperature=45.0), PWMStatus.IDLE
    )
    minimum_setpoint.calculate(
        boiler_state(flow_temperature=None, return_temperature=45.0), PWMStatus.IDLE
    )

    assert minimum_setpoint.current == 41.0


async def test_the_base_return_temperature_is_restored(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="sat", data={**DEFAULT_USER_DATA, "minimum_setpoint": 45}
    )
    hass_storage[f"{DOMAIN}.sat.minimum_setpoint"] = {
        "version": 1,
        "key": f"{DOMAIN}.sat.minimum_setpoint",
        "data": {"base_return_temperature": 40.0},
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    minimum_setpoint = entry.runtime_data.climate.minimum_setpoint

    minimum_setpoint.calculate(boiler_state(return_temperature=45.0), PWMStatus.IDLE)

    assert minimum_setpoint.current == 46.0


async def test_removing_the_entry_deletes_its_storage(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, entry_id="sat", data={**DEFAULT_USER_DATA, "minimum_setpoint": 45}
    )
    hass_storage[f"{DOMAIN}.sat.minimum_setpoint"] = {
        "version": 1,
        "key": f"{DOMAIN}.sat.minimum_setpoint",
        "data": {"base_return_temperature": 40.0},
    }
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert f"{DOMAIN}.sat.minimum_setpoint" not in hass_storage

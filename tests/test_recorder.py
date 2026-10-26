"""Tests for what the recorder keeps of the SAT climate."""

from typing import TYPE_CHECKING

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.recorder import get_instance, history
from homeassistant.core import State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.sat.const import DOMAIN
from tests.const import DEFAULT_USER_DATA

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.typing import RecorderInstanceContextManager


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: RecorderInstanceContextManager, enable_custom_integrations: None
) -> None:
    """Start the recorder before Home Assistant and the custom integrations."""


async def test_volatile_climate_attributes_are_not_recorded(hass: HomeAssistant) -> None:
    hass.states.async_set("sensor.test_inside_sensor", "19.5")
    hass.states.async_set("sensor.test_outside_sensor", "5.0")
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    start = dt_util.utcnow()
    climate = entry.runtime_data.climate
    await entry.runtime_data.coordinator.async_set_boiler_temperature(40)
    await climate.async_set_target_temperature(21.0)
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    await climate.async_control_heating_loop()
    await async_wait_recording_done(hass)

    states = await get_instance(hass).async_add_executor_job(
        history.get_significant_states, hass, start, None, [climate.entity_id]
    )
    state = states[climate.entity_id][-1]
    assert isinstance(state, State)
    attributes = state.attributes
    assert "setpoint" in attributes
    for key in (
        "boiler_temperature_derivative",
        "boiler_temperature_cold",
        "boiler_flame_timing",
        "error",
        "integral",
        "derivative",
        "derivative_raw",
        "proportional",
        "current_kp",
        "current_ki",
        "current_kd",
        "pulse_width_modulation_duty_cycle",
    ):
        assert key in climate.extra_state_attributes
        assert key not in attributes
    assert await hass.config_entries.async_unload(entry.entry_id)

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import section
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sat.config_flow import SatFlowHandler
from custom_components.sat.const import DOMAIN, MODE_FAKE
from tests.const import DEFAULT_USER_DATA


async def test_create_coordinator(hass):
    flow_handler = SatFlowHandler()
    flow_handler.data = {
        "name": "Test",
        "mode": MODE_FAKE,
        "device": "test_device",
    }

    await flow_handler.async_create_coordinator()


async def test_user_menu_without_advanced_mode(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})

    assert "simulator" in result["menu_options"]
    assert "show_advanced_options" not in caplog.text


async def test_options_advanced_section(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["general", "presets", "system_configuration"]

    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "system_configuration"})
    advanced = result["data_schema"].schema["advanced"]
    assert isinstance(advanced, section)
    assert advanced.options["collapsed"] is True

    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "automatic_duty_cycle": True,
        "sync_climates_with_mode": True,
        "sensor_max_value_age": "06:00:00",
        "default_hvac_mode": "heat",
        "window_minimum_open_time": "00:00:15",
        "advanced": {
            "simulation": False,
            "thermal_comfort": True,
            "dynamic_minimum_setpoint": False,
            "climate_valve_offset": 0,
            "target_temperature_step": 0.5,
            "maximum_relative_modulation": 100,
            "sample_time": "00:01:00",
        },
    })
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert entry.options["thermal_comfort"] is True
    assert "advanced" not in entry.options
    assert "show_advanced_options" not in caplog.text
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_cycles_per_hour_follow_the_heating_system(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, version=SatFlowHandler.VERSION, data={**DEFAULT_USER_DATA, "minimum_setpoint": 45, "heating_system": "heat_pump"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "system_configuration"})

    cycles = next(value for key, value in result["data_schema"].schema.items() if key == "cycles_per_hour")
    assert [option["value"] for option in cycles.config["options"]] == ["2", "3"]
    assert await hass.config_entries.async_unload(entry.entry_id)

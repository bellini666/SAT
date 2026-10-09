import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry, entity_registry as er, issue_registry as ir
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    CONF_MODE,
    CONF_NAME,
    CONF_DEVICE,
)
from .climate import SatClimate
from .coordinator import SatDataUpdateCoordinator, SatDataUpdateCoordinatorFactory
from .services import async_setup_services

_LOGGER: logging.Logger = logging.getLogger(__name__)
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.NUMBER, Platform.BINARY_SENSOR, Platform.BUTTON]


@dataclass
class SatRuntimeData:
    coordinator: SatDataUpdateCoordinator
    climate: SatClimate


type SatConfigEntry = ConfigEntry[SatRuntimeData]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SatConfigEntry):
    """
    Set up this integration using the UI.

    This function is called by Home Assistant when the integration is set up with the UI.
    """
    # Resolve the coordinator by using the factory according to the mode
    coordinator = SatDataUpdateCoordinatorFactory().resolve(
        hass=hass, data=entry.data, options=entry.options, mode=entry.data.get(CONF_MODE), device=entry.data.get(CONF_DEVICE)
    )

    # Making sure everything is loaded
    issue_id = f"setup_failed_{entry.entry_id}"
    try:
        await coordinator.async_setup()
    except ConfigEntryNotReady as exception:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="setup_failed",
            translation_placeholders={"title": entry.title, "error": str(exception)},
        )
        raise

    ir.async_delete_issue(hass, DOMAIN, issue_id)

    entry.runtime_data = SatRuntimeData(
        coordinator=coordinator,
        climate=SatClimate(coordinator, entry, hass.config.units.temperature_unit),
    )

    async def async_stop(_event: Event) -> None:
        await async_release_control(entry)

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_stop))

    # Forward entry setup for used platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: SatConfigEntry) -> bool:
    """
    Handle removal of an entry.

    This function is called by Home Assistant when the integration is being removed.
    """
    await async_release_control(entry)

    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.coordinator.async_will_remove_from_hass()

    return unloaded


async def async_release_control(entry: SatConfigEntry) -> None:
    """Hand the boiler back once the running control loop has finished."""
    await entry.runtime_data.climate.async_stop_control()
    await entry.runtime_data.coordinator.async_release_control()


async def async_remove_entry(hass: HomeAssistant, entry: SatConfigEntry) -> None:
    ir.async_delete_issue(hass, DOMAIN, f"setup_failed_{entry.entry_id}")


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    from .config_flow import SatFlowHandler
    _LOGGER.debug("Migrating from version %s", entry.version)

    if entry.version < SatFlowHandler.VERSION:
        new_data = {**entry.data}
        new_options = {**entry.options}

        if entry.version < 2:
            if not entry.data.get("minimum_setpoint"):
                # Legacy Store
                store = Store(hass, 1, DOMAIN)
                new_data["minimum_setpoint"] = 10

                if (data := await store.async_load()) and (overshoot_protection_value := data.get("overshoot_protection_value")):
                    new_data["minimum_setpoint"] = overshoot_protection_value

            if entry.options.get("heating_system") == "underfloor":
                new_data["heating_system"] = "underfloor"
            else:
                new_data["heating_system"] = "radiators"

            if not entry.data.get("maximum_setpoint"):
                new_data["maximum_setpoint"] = 55

                if entry.options.get("heating_system") == "underfloor":
                    new_data["maximum_setpoint"] = 50

                if entry.options.get("heating_system") == "radiator_low_temperatures":
                    new_data["maximum_setpoint"] = 55

                if entry.options.get("heating_system") == "radiator_medium_temperatures":
                    new_data["maximum_setpoint"] = 65

                if entry.options.get("heating_system") == "radiator_high_temperatures":
                    new_data["maximum_setpoint"] = 75

        if entry.version < 3:
            if main_climates := entry.options.get("main_climates"):
                new_data["main_climates"] = main_climates
                new_options.pop("main_climates")

            if secondary_climates := entry.options.get("climates"):
                new_data["secondary_climates"] = secondary_climates
                new_options.pop("climates")

            if sync_with_thermostat := entry.options.get("sync_with_thermostat"):
                new_data["sync_with_thermostat"] = sync_with_thermostat
                new_options.pop("sync_with_thermostat")

        if entry.version < 4:
            if entry.data.get("window_sensor") is not None:
                new_data["window_sensors"] = [entry.data.get("window_sensor")]
                del new_options["window_sensor"]

        if entry.version < 5:
            if entry.options.get("overshoot_protection") is not None:
                new_data["overshoot_protection"] = entry.options.get("overshoot_protection")
                del new_options["overshoot_protection"]

        if entry.version < 7:
            new_options["pid_controller_version"] = 1

        if entry.version < 8:
            if entry.options.get("heating_curve_version") is not None and int(entry.options.get("heating_curve_version")) < 2:
                new_options["heating_curve_version"] = 3

        if entry.version < 9:
            if entry.data.get("heating_system") == "heat_pump":
                new_options["cycles_per_hour"] = 2

            if entry.data.get("heating_system") == "radiators":
                new_options["cycles_per_hour"] = 3

        if entry.version < 10:
            if entry.data.get("mode") == "mqtt":
                device = device_registry.async_get(hass).async_get(entry.data.get("device"))

                new_data["mode"] = "mqtt_opentherm"
                new_data["device"] = list(device.identifiers)[0][1]

        if entry.version < 11:
            if entry.data.get("sync_with_thermostat") is not None:
                new_data["push_setpoint_to_thermostat"] = entry.data.get("sync_with_thermostat")

        if entry.version < 12:
            if "push_setpoint_to_thermostat" in new_data:
                new_options["push_setpoint_to_thermostat"] = new_data.pop("push_setpoint_to_thermostat")

            name = entry.data.get(CONF_NAME)
            prefixes = (f"{name.lower()}-", f"{name}-")

            @callback
            def migrate_unique_id(entity_entry: er.RegistryEntry) -> dict[str, str] | None:
                if entity_entry.unique_id == name.lower():
                    return {"new_unique_id": entry.entry_id}

                for prefix in prefixes:
                    if entity_entry.unique_id.startswith(prefix):
                        return {"new_unique_id": f"{entry.entry_id}-{entity_entry.unique_id.removeprefix(prefix)}"}

                return None

            await er.async_migrate_entries(hass, entry.entry_id, migrate_unique_id)

            devices = device_registry.async_get(hass)
            if device := devices.async_get_device_by_identifier((DOMAIN, name), entry.entry_id):
                devices.async_update_device(device.id, new_identifiers={(DOMAIN, entry.entry_id)})

        hass.config_entries.async_update_entry(entry, version=SatFlowHandler.VERSION, data=new_data, options=new_options)

    _LOGGER.info("Migration to version %s successful", entry.version)

    return True


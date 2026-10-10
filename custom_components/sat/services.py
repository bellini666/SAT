import voluptuous as vol
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, service

from .const import DOMAIN, SERVICE_RESET_INTEGRAL, SERVICE_PULSE_WIDTH_MODULATION


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_RESET_INTEGRAL,
        entity_domain=CLIMATE_DOMAIN,
        schema=None,
        func="async_reset_integral",
    )

    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_PULSE_WIDTH_MODULATION,
        entity_domain=CLIMATE_DOMAIN,
        schema={vol.Required("enabled"): cv.boolean},
        func="async_set_pulse_width_modulation",
    )

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.core import HomeAssistant

from . import SatConfigEntry
from .const import CONF_DEVICE

TO_REDACT = {CONF_DEVICE}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SatConfigEntry) -> dict[str, Any]:
    climate = entry.runtime_data.climate
    coordinator = entry.runtime_data.coordinator

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "climate": {
            "hvac_mode": climate.hvac_mode,
            "preset_mode": climate.preset_mode,
            "target_temperature": climate.target_temperature,
            "setpoint": climate.setpoint,
            "control_problems": climate.control_problems,
            "errors": [asdict(error) for error in climate.errors],
            "attributes": climate.extra_state_attributes,
        },
        "coordinator": {
            "device_type": coordinator.device_type,
            "device_status": coordinator.device_status,
            "boiler": asdict(coordinator.boiler),
            "flame": asdict(coordinator.flame),
            "data": dict(coordinator.data),
            "messages": [
                {**message, "topic": message["topic"].replace(entry.data[CONF_DEVICE], REDACTED)}
                for message in coordinator.messages
            ],
        },
    }

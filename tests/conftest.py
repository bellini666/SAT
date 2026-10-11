"""Fixtures for testing."""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, assert_setup_component

from custom_components.sat.const import DOMAIN
from custom_components.sat.fake import SatFakeCoordinator
from tests.const import DEFAULT_USER_DATA

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

    from custom_components.sat import SatConfigEntry
    from custom_components.sat.climate import SatClimate


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the custom integrations from this repository in every test."""


@pytest.fixture
async def instant_mqtt_command_delay(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[None]:
    async def sleep(_delay: float) -> None:
        pass

    monkeypatch.setattr("custom_components.sat.mqtt.asyncio", SimpleNamespace(sleep=sleep))
    yield

    # The MQTT client misc timer stops on its first run against the paho mock,
    # one second after it starts
    await asyncio.sleep(1.1)
    await hass.async_block_till_done()


@pytest.fixture
async def entry(
    hass: HomeAssistant,
    domains: list[tuple[str, int]],
    data: dict[str, Any],
    options: dict[str, Any],
    config: dict[str, Any],
) -> MockConfigEntry:
    """Set up the given integrations and add a SAT config entry."""
    for domain, count in domains:
        with assert_setup_component(count, domain):
            assert await async_setup_component(hass, domain, config)

        await hass.async_block_till_done()

    await hass.async_start()
    await hass.async_block_till_done()

    user_data = DEFAULT_USER_DATA.copy()
    user_data.update(data)

    config_entry = MockConfigEntry(domain=DOMAIN, data=user_data, options=options)
    await hass.config_entries.async_add(config_entry)

    return config_entry


@pytest.fixture
async def climate(entry: SatConfigEntry) -> SatClimate:
    return entry.runtime_data.climate


@pytest.fixture
async def coordinator(entry: SatConfigEntry) -> SatFakeCoordinator:
    coordinator = entry.runtime_data.coordinator
    assert isinstance(coordinator, SatFakeCoordinator)
    return coordinator

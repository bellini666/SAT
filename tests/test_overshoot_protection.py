"""Tests for the overshoot protection value calibration."""

import asyncio
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.sat.const import CONF_MAXIMUM_SETPOINT, HEATING_SYSTEM_RADIATORS, MINIMUM_SETPOINT, OPTIONS_DEFAULTS
from custom_components.sat.coordinator import DeviceState
from custom_components.sat.manufacturer import Manufacturer
from custom_components.sat.manufacturers.geminox import Geminox
from custom_components.sat.overshoot_protection import CalibrationError, OvershootProtection
from custom_components.sat.simulator import SatSimulatorCoordinator


class Boiler:
    """The coordinator surface calibration talks to."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.flame_active = False
        self.hot_water_active = False
        self.boiler_temperature: float | None = 30.0
        self.relative_modulation_value: float | None = 0.0
        self.minimum_relative_modulation_value: float | None = 12.0
        self.manufacturer: Manufacturer | None = None
        self.commands: list[tuple[str, object]] = []
        self.gate: asyncio.Event | None = None
        self.error: Exception | None = None

    async def async_set_heater_state(self, state: DeviceState) -> None:
        self.commands.append(("CH", state))

    async def async_set_control_setpoint(self, value: float) -> None:
        if self.gate is not None:
            await self.gate.wait()
        self.commands.append(("CS", value))

    async def async_set_control_max_relative_modulation(self, value: int) -> None:
        if self.error is not None:
            raise self.error
        self.commands.append(("MM", value))

    async def async_release_control(self) -> None:
        self.commands.append(("release", None))

    async def async_control_heating_loop(self) -> None:
        pass


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, count: int = 1) -> None:
    for _ in range(count):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def start(hass: HomeAssistant, boiler: Boiler, **kwargs) -> tuple[OvershootProtection, asyncio.Task]:
    protection = OvershootProtection(boiler, HEATING_SYSTEM_RADIATORS, maximum_setpoint=55, **kwargs)
    return protection, hass.async_create_background_task(protection.calculate(), "calibration")


def released(boiler: Boiler) -> bool:
    return boiler.commands[-3:] == [("CH", DeviceState.OFF), ("CS", MINIMUM_SETPOINT), ("release", None)]


async def heat_to_plateau(hass: HomeAssistant, freezer: FrozenDateTimeFactory, boiler: Boiler, flow: float, modulation: float) -> None:
    boiler.flame_active = True
    boiler.relative_modulation_value = 60
    for temperature in (35, 40, 44):
        boiler.boiler_temperature = temperature
        await tick(hass, freezer)

    boiler.relative_modulation_value = modulation
    for offset in (0.0, 0.2, 0.3, 0.1, 0.2, 0.3, 0.0, 0.2, 0.1, 0.3, 0.2, 0.1):
        boiler.boiler_temperature = flow + offset
        await tick(hass, freezer)


async def test_capped_at_the_maximum_setpoint(hass: HomeAssistant) -> None:
    protection = OvershootProtection(Boiler(hass), HEATING_SYSTEM_RADIATORS, maximum_setpoint=55)

    assert protection.setpoint == 55


@pytest.mark.parametrize(("flow", "modulation", "method", "low", "high"), [
    (45.0, 13, "minimum_modulation", 45.0, 45.3),
    (47.0, 0, "zero_modulation", 47.0, 47.3),
    (54.8, 40, "formula", 33.0, 33.0),
])
async def test_measures_the_plateau(hass: HomeAssistant, freezer: FrozenDateTimeFactory, flow: float, modulation: float, method: str, low: float, high: float) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler)
    await hass.async_block_till_done()

    await heat_to_plateau(hass, freezer, boiler, flow=flow, modulation=modulation)

    result = await task
    assert result.method == method
    assert low <= result.value <= high
    assert ("CS", 55) in boiler.commands
    assert ("MM", 0) in boiler.commands
    assert released(boiler)


async def test_a_rise_of_0_1_degrees_per_minute_is_not_a_plateau(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler)

    boiler.flame_active = True
    boiler.relative_modulation_value = 13
    for step in range(30):
        boiler.boiler_temperature = 43.5 + step * 0.05
        await tick(hass, freezer)

    for offset in (0.0, 0.2, 0.3, 0.1, 0.2, 0.3, 0.0, 0.2, 0.1, 0.3, 0.2, 0.1):
        boiler.boiler_temperature = 45.0 + offset
        await tick(hass, freezer)

    result = await task
    assert result.method == "minimum_modulation"
    assert 45.0 <= result.value <= 45.3


async def test_whole_degree_flicker_is_a_plateau(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler)

    boiler.flame_active = True
    boiler.relative_modulation_value = 13
    for flow in (35, 40, 44, 46, 47, 48, 47, 48, 47, 47, 48, 47, 48, 48, 47, 48, 47):
        boiler.boiler_temperature = float(flow)
        await tick(hass, freezer)

    assert task.done()
    result = await task
    assert result.method == "minimum_modulation"
    assert 47.0 <= result.value <= 48.0


@pytest.mark.parametrize(("minimum", "ramp", "plateau", "method", "value"), [
    (14.0, 0, 16, "formula", 46.2),
    (None, 13, 13, "minimum_modulation", 45.2),
])
async def test_modulation_floor_is_the_lowest_level_seen(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minimum: float | None, ramp: float, plateau: float, method: str, value: float) -> None:
    boiler = Boiler(hass)
    boiler.minimum_relative_modulation_value = minimum
    _protection, task = start(hass, boiler)

    boiler.flame_active = True
    boiler.relative_modulation_value = ramp
    for flow in (35, 40, 44):
        boiler.boiler_temperature = flow
        await tick(hass, freezer)

    boiler.relative_modulation_value = plateau
    for offset in (0.0, 0.2, 0.3, 0.1, 0.2, 0.3, 0.0, 0.2, 0.1, 0.3, 0.2, 0.1):
        boiler.boiler_temperature = 45.0 + offset
        await tick(hass, freezer)

    result = await task
    assert (result.method, result.value) == (method, value)


async def test_fails_when_the_floor_plateau_reaches_the_setpoint(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler)

    await heat_to_plateau(hass, freezer, boiler, flow=54.0, modulation=13)

    with pytest.raises(CalibrationError, match="setpoint_reached"):
        await task
    assert released(boiler)


async def test_hot_water_pauses_the_measurement(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    protection, task = start(hass, boiler, plateau_timeout=timedelta(minutes=10))

    boiler.flame_active = True
    boiler.relative_modulation_value = 13
    boiler.boiler_temperature = 45.0
    await tick(hass, freezer, 4)

    boiler.hot_water_active = True
    boiler.boiler_temperature = 60.0
    sent = len(boiler.commands)
    await tick(hass, freezer, 40)
    assert protection.phase == "paused"
    assert boiler.commands[sent:] == [("release", None)]
    assert not task.done()

    boiler.hot_water_active = False
    await heat_to_plateau(hass, freezer, boiler, flow=45.0, modulation=13)

    result = await task
    assert result.method == "minimum_modulation"


async def test_times_out_without_a_plateau(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler, plateau_timeout=timedelta(minutes=10))

    boiler.flame_active = True
    boiler.relative_modulation_value = 13
    for step in range(25):
        boiler.boiler_temperature = 30.0 + step
        await tick(hass, freezer)

    with pytest.raises(CalibrationError, match="timeout"):
        await task
    assert released(boiler)


async def test_hot_water_counts_towards_the_overall_deadline(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    boiler.hot_water_active = True
    _protection, task = start(hass, boiler, flame_timeout=timedelta(minutes=5), plateau_timeout=timedelta(minutes=10))

    await tick(hass, freezer, 59)
    assert not task.done()

    await tick(hass, freezer, 2)
    assert task.done()
    with pytest.raises(CalibrationError, match="timeout"):
        await task
    assert released(boiler)


async def test_fails_early_without_modulation(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    boiler.relative_modulation_value = None
    _protection, task = start(hass, boiler)

    boiler.flame_active = True
    boiler.boiler_temperature = 45.0
    await tick(hass, freezer, 11)

    assert task.done()
    with pytest.raises(CalibrationError, match="no_modulation"):
        await task
    assert released(boiler)


async def test_fails_without_a_flame(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler, flame_timeout=timedelta(minutes=5))

    await tick(hass, freezer, 12)

    with pytest.raises(CalibrationError, match="no_flame"):
        await task
    assert released(boiler)


async def test_fails_after_repeated_flame_losses(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler)

    for _ in range(4):
        boiler.flame_active = True
        await tick(hass, freezer, 2)
        boiler.flame_active = False
        await tick(hass, freezer)

    with pytest.raises(CalibrationError, match="flame_lost"):
        await task
    assert released(boiler)


async def test_abort_releases_the_overrides(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler)
    await tick(hass, freezer, 2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert released(boiler)


async def test_refreshes_the_control_setpoint_every_tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler)
    await hass.async_block_till_done()
    await tick(hass, freezer, 4)

    assert boiler.commands.count(("CS", 55)) == 5
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_release_waits_for_a_running_tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler)
    await tick(hass, freezer)

    boiler.gate = asyncio.Event()
    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await asyncio.sleep(0)

    task.cancel()
    await asyncio.sleep(0)
    boiler.gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    await hass.async_block_till_done()

    assert released(boiler)


async def test_gateway_error_ends_the_calibration(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    _protection, task = start(hass, boiler)
    await tick(hass, freezer)

    boiler.error = RuntimeError("gateway")
    await tick(hass, freezer)

    with pytest.raises(RuntimeError, match="gateway"):
        await task
    assert released(boiler)


async def test_simulator_heats_during_calibration(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    simulator = SatSimulatorCoordinator(hass, {**OPTIONS_DEFAULTS, CONF_MAXIMUM_SETPOINT: 55})
    _protection, task = start(hass, simulator)

    await tick(hass, freezer, 4)

    assert simulator.boiler_temperature > MINIMUM_SETPOINT
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_geminox_gets_the_climate_modulation_clamp(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    boiler = Boiler(hass)
    boiler.manufacturer = Geminox()
    _protection, task = start(hass, boiler)
    await tick(hass, freezer)

    assert ("MM", 10) in boiler.commands
    assert ("MM", 0) not in boiler.commands
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

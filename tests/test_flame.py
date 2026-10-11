"""Tests for the flame cycle statistics."""

from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory

from custom_components.sat.boiler import BoilerState
from custom_components.sat.const import BoilerStatus, FlameStatus
from custom_components.sat.flame import Flame


def boiler_state(flame_active: bool, demand: bool = True, hot_water: bool = False) -> BoilerState:
    if hot_water:
        status = BoilerStatus.HOT_WATER
    elif not demand:
        status = BoilerStatus.IDLE
    else:
        status = BoilerStatus.AT_SETPOINT if flame_active else BoilerStatus.WAITING_FOR_FLAME

    return BoilerState(
        is_active=demand,
        is_inactive=not demand,
        status=status,
        flame_active=flame_active,
        hot_water_active=hot_water,
        setpoint=50.0,
        flow_temperature=50.0,
        return_temperature=40.0,
        relative_modulation_level=30.0,
    )


def run_cycle(flame: Flame, freezer: FrozenDateTimeFactory, on_seconds: int) -> None:
    flame.update(boiler_state(True))
    for _ in range(on_seconds // 10):
        freezer.tick(timedelta(seconds=10))
        flame.update(boiler_state(True))

    flame.update(boiler_state(False))
    freezer.tick(timedelta(seconds=600))
    flame.update(boiler_state(False))


def test_average_on_time_uses_completed_cycles(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame(smoothing_alpha=0.5)

    run_cycle(flame, freezer, 300)
    assert flame.average_on_time_seconds == 300.0

    run_cycle(flame, freezer, 100)
    assert flame.average_on_time_seconds == 200.0


def idle(flame: Flame, freezer: FrozenDateTimeFactory, seconds: int) -> None:
    for _ in range(seconds // 60):
        freezer.tick(timedelta(seconds=60))
        flame.update(boiler_state(False, demand=False))


def wait_for_flame(flame: Flame, freezer: FrozenDateTimeFactory, seconds: int, hot_water: bool = False) -> None:
    flame.update(boiler_state(False, hot_water=hot_water))
    for _ in range(seconds // 10):
        freezer.tick(timedelta(seconds=10))
        flame.update(boiler_state(False, hot_water=hot_water))


@pytest.mark.parametrize("cycles", [1, 3], ids=["thin_history", "full_history"])
def test_demand_after_a_long_idle_is_not_stuck_off(freezer: FrozenDateTimeFactory, cycles: int) -> None:
    flame = Flame()
    for _ in range(cycles):
        run_cycle(flame, freezer, 300)

    idle(flame, freezer, 3600)
    wait_for_flame(flame, freezer, 20)

    assert flame.health_status != FlameStatus.STUCK_OFF


@pytest.mark.parametrize("cycles", [1, 3], ids=["thin_history", "full_history"])
def test_demand_without_flame_is_stuck_off(freezer: FrozenDateTimeFactory, cycles: int) -> None:
    flame = Flame()
    for _ in range(cycles):
        run_cycle(flame, freezer, 300)

    idle(flame, freezer, 3600)
    wait_for_flame(flame, freezer, 360)

    assert flame.health_status == FlameStatus.STUCK_OFF


def test_hot_water_after_a_long_idle_is_not_stuck_off(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame()
    for _ in range(3):
        run_cycle(flame, freezer, 300)

    idle(flame, freezer, 3600)
    wait_for_flame(flame, freezer, 10, hot_water=True)

    assert flame.health_status != FlameStatus.STUCK_OFF


def test_stuck_off_clears_when_the_flame_lights(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame()
    run_cycle(flame, freezer, 300)
    wait_for_flame(flame, freezer, 360)
    assert flame.health_status == FlameStatus.STUCK_OFF

    flame.update(boiler_state(True))

    assert flame.health_status != FlameStatus.STUCK_OFF

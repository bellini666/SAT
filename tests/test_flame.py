"""Tests for the flame cycle statistics."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory

from custom_components.sat.boiler import BoilerState
from custom_components.sat.const import BoilerStatus
from custom_components.sat.flame import Flame


def boiler_state(flame_active: bool) -> BoilerState:
    return BoilerState(
        is_active=True,
        is_inactive=False,
        status=BoilerStatus.AT_SETPOINT if flame_active else BoilerStatus.WAITING_FOR_FLAME,
        flame_active=flame_active,
        hot_water_active=False,
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

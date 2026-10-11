"""Tests for the flame cycle statistics."""

from datetime import timedelta

import logging

import pytest
from freezegun.api import FrozenDateTimeFactory

from custom_components.sat.boiler import BoilerState
from custom_components.sat.const import BoilerStatus, FlameStatus, PWMStatus
from custom_components.sat.flame import Flame
from custom_components.sat.pwm import PWMState

PWM_ON = PWMState(enabled=True, status=PWMStatus.ON, duty_cycle=(900, 300))
PWM_OFF = PWMState(enabled=True, status=PWMStatus.OFF, duty_cycle=(900, 300))


def boiler_state(flame_active: bool, demand: bool = True, hot_water: bool = False, setpoint: float = 50.0, flow: float = 50.0) -> BoilerState:
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
        setpoint=setpoint,
        flow_temperature=flow,
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


@pytest.mark.parametrize(("cycles", "hot_water"), [(1, False), (3, False), (3, True)], ids=["thin_history", "full_history", "hot_water"])
def test_demand_after_a_long_idle_is_not_stuck_off(freezer: FrozenDateTimeFactory, cycles: int, hot_water: bool) -> None:
    flame = Flame()
    for _ in range(cycles):
        run_cycle(flame, freezer, 300)

    idle(flame, freezer, 3600)
    wait_for_flame(flame, freezer, 20, hot_water=hot_water)

    assert flame.health_status != FlameStatus.STUCK_OFF


@pytest.mark.parametrize("cycles", [1, 3], ids=["thin_history", "full_history"])
def test_demand_without_flame_is_stuck_off(freezer: FrozenDateTimeFactory, cycles: int) -> None:
    flame = Flame()
    for _ in range(cycles):
        run_cycle(flame, freezer, 300)

    idle(flame, freezer, 3600)
    wait_for_flame(flame, freezer, 360)

    assert flame.health_status == FlameStatus.STUCK_OFF


def test_stuck_off_clears_when_the_flame_lights(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame()
    run_cycle(flame, freezer, 300)
    wait_for_flame(flame, freezer, 360)
    assert flame.health_status == FlameStatus.STUCK_OFF

    flame.update(boiler_state(True))

    assert flame.health_status != FlameStatus.STUCK_OFF


def run_pwm_cycle(flame: Flame, freezer: FrozenDateTimeFactory, on_seconds: int, pwm_on: PWMState = PWM_ON, cut: bool = False) -> None:
    flame.update(boiler_state(True), pwm_on)
    for _ in range(on_seconds // 10):
        freezer.tick(timedelta(seconds=10))
        flame.update(boiler_state(True), pwm_on)

    if not cut:
        flame.update(boiler_state(True), PWM_OFF)

    flame.update(boiler_state(False), PWM_OFF if not cut else pwm_on)
    freezer.tick(timedelta(seconds=300))
    flame.update(boiler_state(False, demand=False), PWM_OFF)


def start_pwm_burn(flame: Flame) -> None:
    flame.update(boiler_state(False), PWM_ON)
    flame.update(boiler_state(True), PWM_ON)


def test_older_cut_burns_do_not_flag_full_pwm_burns(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame()
    for _ in range(3):
        run_pwm_cycle(flame, freezer, 30, cut=True)
    for _ in range(2):
        run_pwm_cycle(flame, freezer, 900)

    start_pwm_burn(flame)

    assert flame.health_status == FlameStatus.HEALTHY


def test_burns_the_boiler_cuts_short_are_pwm_short(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame()
    run_pwm_cycle(flame, freezer, 900)
    run_pwm_cycle(flame, freezer, 30, cut=True)
    run_pwm_cycle(flame, freezer, 30, cut=True)

    start_pwm_burn(flame)

    assert flame.health_status == FlameStatus.PWM_SHORT


def test_a_burn_pwm_ends_early_is_not_cut_short(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame()
    for _ in range(3):
        run_pwm_cycle(flame, freezer, 200)

    start_pwm_burn(flame)

    assert flame.health_status == FlameStatus.HEALTHY


def test_full_duty_burns_the_boiler_ends_early_are_pwm_short(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame()
    full = PWMState(enabled=True, status=PWMStatus.ON, duty_cycle=(1800, 0))
    run_pwm_cycle(flame, freezer, 1800, pwm_on=full, cut=True)
    for _ in range(2):
        run_pwm_cycle(flame, freezer, 30, pwm_on=full, cut=True)

    flame.update(boiler_state(False), full)
    flame.update(boiler_state(True), full)

    assert flame.health_status == FlameStatus.PWM_SHORT


def test_hot_water_burns_during_pwm_are_not_cut_short(freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture) -> None:
    flame = Flame()
    run_pwm_cycle(flame, freezer, 900)

    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            flame.update(boiler_state(False, hot_water=True, setpoint=40.0, flow=40.0), PWM_ON)
            flame.update(boiler_state(True, hot_water=True, setpoint=40.0, flow=60.0), PWM_ON)
            freezer.tick(timedelta(seconds=120))
            flame.update(boiler_state(False, hot_water=True, setpoint=40.0, flow=55.0), PWM_ON)
            freezer.tick(timedelta(seconds=60))

    start_pwm_burn(flame)

    assert flame.health_status == FlameStatus.HEALTHY
    assert "overshoot protection" not in caplog.text


def test_a_stale_pwm_state_does_not_judge_burns(freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture) -> None:
    flame = Flame()
    run_pwm_cycle(flame, freezer, 900)
    flame.update(boiler_state(False), PWM_ON)
    freezer.tick(timedelta(minutes=5))

    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            flame.update(boiler_state(True, setpoint=40.0, flow=50.0))
            freezer.tick(timedelta(seconds=30))
            flame.update(boiler_state(False, setpoint=40.0, flow=45.0))
            freezer.tick(timedelta(minutes=5))

    start_pwm_burn(flame)

    assert flame.health_status == FlameStatus.HEALTHY
    assert "overshoot protection" not in caplog.text


def test_cut_burns_older_than_four_hours_are_forgotten(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame()
    for _ in range(2):
        run_pwm_cycle(flame, freezer, 30, cut=True)

    freezer.tick(timedelta(hours=5))
    for _ in range(3):
        run_cycle(flame, freezer, 300)

    start_pwm_burn(flame)

    assert flame.health_status == FlameStatus.HEALTHY


def ignition_spike(flame: Flame, freezer: FrozenDateTimeFactory, pwm_off_first: bool) -> None:
    flame.update(boiler_state(False, setpoint=40.0, flow=35.0), PWM_ON)
    for flow in (35.0, 41.0, 46.5, 44.0):
        flame.update(boiler_state(True, setpoint=40.0, flow=flow), PWM_ON)
        freezer.tick(timedelta(seconds=10))

    flame.update(boiler_state(False, setpoint=40.0, flow=43.0), PWM_OFF if pwm_off_first else PWM_ON)


@pytest.mark.parametrize("pwm_off_first", [False, True], ids=["boiler_cut", "pwm_ended"])
def test_an_ignition_spike_warns_only_when_the_boiler_cuts_the_burn(freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture, pwm_off_first: bool) -> None:
    flame = Flame()

    with caplog.at_level(logging.WARNING):
        ignition_spike(flame, freezer, pwm_off_first=pwm_off_first)

    if pwm_off_first:
        assert "overshoot protection" not in caplog.text
        assert flame.last_cut_short_peak_flow is None
    else:
        assert "raise the overshoot protection value" in caplog.text
        assert (flame.last_cut_short_peak_flow, flame.last_cut_short_setpoint) == (46.5, 40.0)

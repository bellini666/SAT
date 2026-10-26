"""Tests for the flame cycle statistics."""

import logging
from datetime import timedelta
from time import time
from typing import TYPE_CHECKING, Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.sat.boiler import BoilerState
from custom_components.sat.const import DOMAIN, BoilerStatus, FlameStatus, PWMStatus
from custom_components.sat.flame import Flame
from custom_components.sat.pwm import PWMState
from tests.const import DEFAULT_USER_DATA

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant

PWM_ON = PWMState(enabled=True, status=PWMStatus.ON, duty_cycle=(900, 300))
PWM_OFF = PWMState(enabled=True, status=PWMStatus.OFF, duty_cycle=(900, 300))


def boiler_state(
    flame_active: bool,
    demand: bool = True,
    hot_water: bool = False,
    setpoint: float = 50.0,
    flow: float = 50.0,
    status: BoilerStatus | None = None,
) -> BoilerState:
    if status is None:
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


def wait_for_flame(
    flame: Flame, freezer: FrozenDateTimeFactory, seconds: int, hot_water: bool = False
) -> None:
    flame.update(boiler_state(False, hot_water=hot_water))
    for _ in range(seconds // 10):
        freezer.tick(timedelta(seconds=10))
        flame.update(boiler_state(False, hot_water=hot_water))


@pytest.mark.parametrize(
    ("cycles", "hot_water"),
    [(1, False), (3, False), (3, True)],
    ids=["thin_history", "full_history", "hot_water"],
)
def test_demand_after_a_long_idle_is_not_stuck_off(
    freezer: FrozenDateTimeFactory, cycles: int, hot_water: bool
) -> None:
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


@pytest.mark.parametrize("cycles", [0, 3], ids=["thin_history", "full_history"])
def test_a_flame_without_demand_is_stuck_on_from_the_end_of_demand(
    freezer: FrozenDateTimeFactory, cycles: int
) -> None:
    flame = Flame()
    for _ in range(cycles):
        run_cycle(flame, freezer, 300)

    flame.update(boiler_state(True, hot_water=True))
    for _ in range(81):
        freezer.tick(timedelta(seconds=10))
        flame.update(boiler_state(True, hot_water=True))

    freezer.tick(timedelta(seconds=10))
    flame.update(boiler_state(True, demand=False))
    assert flame.health_status == FlameStatus.IDLE_OK

    freezer.tick(timedelta(seconds=130))
    flame.update(boiler_state(True, demand=False))
    assert flame.health_status == FlameStatus.STUCK_ON


@pytest.mark.parametrize("cycles", [0, 3], ids=["thin_history", "full_history"])
def test_a_burn_without_demand_after_a_long_burn_is_not_stuck_on_at_ignition(
    freezer: FrozenDateTimeFactory, cycles: int
) -> None:
    flame = Flame()
    for _ in range(cycles):
        run_cycle(flame, freezer, 300)
    run_cycle(flame, freezer, 900)
    idle(flame, freezer, 600)

    flame.update(boiler_state(True, demand=False))

    assert flame.health_status == FlameStatus.IDLE_OK


@pytest.mark.parametrize("cycles", [0, 3], ids=["thin_history", "full_history"])
def test_a_boiler_waiting_to_relight_after_a_burn_is_not_stuck_off(
    freezer: FrozenDateTimeFactory, cycles: int
) -> None:
    """Replay a Remeha relight that keeps the CH mode bit on while it waits.

    The flame went out at 04:38:10, the flow dropped below the setpoint at 04:41:33 and the
    burner lit again at 04:44:13.
    """
    flame = Flame()
    for _ in range(cycles):
        run_cycle(flame, freezer, 300)

    flame.update(boiler_state(True, setpoint=45.0, flow=52.7))
    for _ in range(20):
        freezer.tick(timedelta(seconds=10))
        flame.update(
            boiler_state(False, setpoint=45.0, flow=48.0, status=BoilerStatus.ANTI_CYCLING)
        )
        assert flame.health_status != FlameStatus.STUCK_OFF

    for _ in range(16):
        freezer.tick(timedelta(seconds=10))
        flame.update(boiler_state(False, setpoint=45.0, flow=41.0))
        assert flame.health_status != FlameStatus.STUCK_OFF

    flame.update(boiler_state(True, setpoint=45.0, flow=39.7))
    assert flame.health_status != FlameStatus.STUCK_OFF


@pytest.mark.parametrize("cycles", [0, 3], ids=["thin_history", "full_history"])
def test_a_burner_that_never_lights_across_short_pwm_phases_is_stuck_off(
    freezer: FrozenDateTimeFactory, cycles: int
) -> None:
    flame = Flame()
    for _ in range(cycles):
        run_cycle(flame, freezer, 300)

    for _ in range(4):
        # The boiler reports the OFF phase setpoint until the new one is echoed back
        flame.update(boiler_state(False, status=BoilerStatus.IDLE), pwm_state=PWM_ON)
        for _ in range(13):
            freezer.tick(timedelta(seconds=10))
            flame.update(boiler_state(False), pwm_state=PWM_ON)
        for _ in range(12):
            freezer.tick(timedelta(seconds=10))
            flame.update(boiler_state(False, demand=False), pwm_state=PWM_OFF)

    flame.update(boiler_state(False), pwm_state=PWM_ON)

    assert flame.health_status == FlameStatus.STUCK_OFF


def test_stuck_off_clears_when_the_flame_lights(freezer: FrozenDateTimeFactory) -> None:
    flame = Flame()
    run_cycle(flame, freezer, 300)
    wait_for_flame(flame, freezer, 360)
    assert flame.health_status == FlameStatus.STUCK_OFF

    flame.update(boiler_state(True))

    assert flame.health_status != FlameStatus.STUCK_OFF


def run_pwm_cycle(
    flame: Flame,
    freezer: FrozenDateTimeFactory,
    on_seconds: int,
    pwm_on: PWMState = PWM_ON,
    cut: bool = False,
) -> None:
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


def test_full_duty_burns_the_boiler_ends_early_are_pwm_short(
    freezer: FrozenDateTimeFactory,
) -> None:
    flame = Flame()
    full = PWMState(enabled=True, status=PWMStatus.ON, duty_cycle=(1800, 0))
    run_pwm_cycle(flame, freezer, 1800, pwm_on=full, cut=True)
    for _ in range(2):
        run_pwm_cycle(flame, freezer, 30, pwm_on=full, cut=True)

    flame.update(boiler_state(False), full)
    flame.update(boiler_state(True), full)

    assert flame.health_status == FlameStatus.PWM_SHORT


def test_hot_water_burns_during_pwm_are_not_cut_short(
    freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
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


def test_a_stale_pwm_state_does_not_judge_burns(
    freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
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

    flame.update(
        boiler_state(False, setpoint=40.0, flow=43.0), PWM_OFF if pwm_off_first else PWM_ON
    )


@pytest.mark.parametrize("pwm_off_first", [False, True], ids=["boiler_cut", "pwm_ended"])
def test_an_ignition_spike_warns_only_when_the_boiler_cuts_the_burn(
    freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture, pwm_off_first: bool
) -> None:
    flame = Flame()

    with caplog.at_level(logging.WARNING):
        ignition_spike(flame, freezer, pwm_off_first=pwm_off_first)

    if pwm_off_first:
        assert "overshoot protection" not in caplog.text
        assert flame.last_cut_short_peak_flow is None
    else:
        assert "raise the overshoot protection value" in caplog.text
        assert (flame.last_cut_short_peak_flow, flame.last_cut_short_setpoint) == (46.5, 40.0)


def test_a_burn_the_boiler_ends_after_the_ignition_spike_settled_does_not_warn(
    freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    flame = Flame()

    with caplog.at_level(logging.WARNING):
        flame.update(boiler_state(False, setpoint=45.0, flow=38.2), PWM_ON)
        for flow in (43.5, 49.7, 43.4, 42.6, 42.2):
            flame.update(boiler_state(True, setpoint=45.0, flow=flow), PWM_ON)
            freezer.tick(timedelta(seconds=50))
        flame.update(boiler_state(False, setpoint=45.0, flow=42.2), PWM_ON)

    assert "overshoot protection" not in caplog.text
    assert flame.last_cut_short_peak_flow is None


def test_a_cut_out_seen_with_the_flow_after_it_still_warns(
    freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    flame = Flame()

    with caplog.at_level(logging.WARNING):
        flame.update(boiler_state(False, setpoint=40.0, flow=34.5), PWM_ON)
        for flow in (40.8, 44.0, 44.8, 45.5, 46.3, 46.1, 44.1):
            flame.update(boiler_state(True, setpoint=40.0, flow=flow), PWM_ON)
            freezer.tick(timedelta(seconds=5))
        flame.update(boiler_state(False, setpoint=40.0, flow=38.4), PWM_ON)

    assert "raise the overshoot protection value" in caplog.text
    assert (flame.last_cut_short_peak_flow, flame.last_cut_short_setpoint) == (46.3, 40.0)


def test_a_hot_water_request_that_ends_before_the_flame_is_seen_is_a_hot_water_burn(
    freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    flame = Flame()
    run_cycle(flame, freezer, 300)

    with caplog.at_level(logging.WARNING):
        flame.update(boiler_state(False, hot_water=True, setpoint=10.0, flow=38.1))
        freezer.tick(timedelta(seconds=6))
        for flow in (44.4, 48.1, 52.7, 56.4, 60.5, 63.5, 66.1):
            flame.update(boiler_state(True, setpoint=45.0, flow=flow), PWM_ON)
            freezer.tick(timedelta(seconds=5))
        flame.update(boiler_state(False, setpoint=45.0, flow=66.8), PWM_ON)

    assert "overshoot protection" not in caplog.text
    assert (flame.sample_count_4h, flame.last_cycle_duration_seconds) == (1, 300.0)


def test_a_burn_far_above_its_setpoint_is_an_unflagged_hot_water_burn(
    freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    flame = Flame()
    run_cycle(flame, freezer, 300)

    with caplog.at_level(logging.WARNING):
        flame.update(boiler_state(False, setpoint=45.0, flow=42.0), PWM_ON)
        for flow in (49.1, 52.9, 57.5, 61.7, 65.4, 67.6):
            flame.update(boiler_state(True, setpoint=45.0, flow=flow), PWM_ON)
            freezer.tick(timedelta(seconds=5))
        flame.update(boiler_state(False, setpoint=45.0, flow=67.3), PWM_ON)

    assert "overshoot protection" not in caplog.text
    assert (flame.sample_count_4h, flame.cut_short_burn_ends) == (1, ())


@pytest.mark.parametrize(
    "readings",
    [
        ((45.0, 35.0), (45.0, 48.0), (45.0, 48.0), (10.0, 48.0)),
        ((30.0, 30.0), (45.0, 40.0), (45.0, 52.0), (45.0, 50.0)),
        ((45.0, 65.0), (45.0, 65.3), (45.0, 55.0), (45.0, 48.0)),
    ],
    ids=[
        "setpoint_dropped_before_the_flame",
        "setpoint_raised_during_the_burn",
        "ignited_on_a_hot_exchanger",
    ],
)
def test_a_heating_burn_stays_in_the_cycle_statistics(
    freezer: FrozenDateTimeFactory, readings: tuple[tuple[float, float], ...]
) -> None:
    flame = Flame()

    flame.update(boiler_state(False, setpoint=45.0, flow=40.0), PWM_ON)
    for setpoint, flow in readings:
        flame.update(boiler_state(True, setpoint=setpoint, flow=flow), PWM_ON)
        freezer.tick(timedelta(seconds=300))
    flame.update(boiler_state(False, setpoint=10.0, flow=47.0), PWM_OFF)

    assert flame.sample_count_4h == 1


def test_a_heating_burn_right_after_a_hot_water_burn_is_judged_as_heating(
    freezer: FrozenDateTimeFactory, caplog: pytest.LogCaptureFixture
) -> None:
    flame = Flame()
    flame.update(boiler_state(True, hot_water=True))
    freezer.tick(timedelta(seconds=15))
    flame.update(boiler_state(False, hot_water=True))
    freezer.tick(timedelta(seconds=20))

    with caplog.at_level(logging.WARNING):
        ignition_spike(flame, freezer, pwm_off_first=False)

    assert "raise the overshoot protection value" in caplog.text


@pytest.mark.parametrize("hot_water_from_ignition", [True, False], ids=["keep_warm", "mid_burn"])
def test_hot_water_burns_are_left_out_of_the_cycle_statistics(
    freezer: FrozenDateTimeFactory, hot_water_from_ignition: bool
) -> None:
    flame = Flame()
    run_cycle(flame, freezer, 300)

    flame.update(boiler_state(True, hot_water=hot_water_from_ignition))
    freezer.tick(timedelta(seconds=15))
    flame.update(boiler_state(True, hot_water=True))
    flame.update(boiler_state(False, hot_water=True))

    assert (
        flame.cycles_last_hour,
        flame.sample_count_4h,
        flame.median_on_duration_seconds_4h,
        flame.average_on_time_seconds,
        flame.last_cycle_duration_seconds,
    ) == (1.0, 1, 300.0, 300.0, 300.0)


def statistics(flame: Flame) -> tuple[Any, ...]:
    return (
        flame.average_on_time_seconds,
        flame.last_cycle_duration_seconds,
        flame.sample_count_4h,
        flame.cycles_last_hour,
        flame.duty_ratio_last_15m,
        flame.median_on_duration_seconds_4h,
        flame.last_cut_short_peak_flow,
        flame.last_cut_short_setpoint,
    )


async def test_cycle_statistics_survive_a_reload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    flame = Flame(smoothing_alpha=0.5)
    await flame.async_initialize(hass, "sat")
    run_cycle(flame, freezer, 300)
    run_cycle(flame, freezer, 100)
    ignition_spike(flame, freezer, pwm_off_first=False)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    restored = Flame(smoothing_alpha=0.5)
    await restored.async_initialize(hass, "sat")

    assert statistics(restored) == statistics(flame)
    assert restored.sample_count_4h == 3

    freezer.tick(timedelta(hours=1, minutes=1))

    assert restored.cycles_last_hour == 0
    assert restored.sample_count_4h == 3

    ignition_spike(restored, freezer, pwm_off_first=False)

    assert restored.health_status == FlameStatus.PWM_SHORT


async def test_initializing_again_does_not_count_the_stored_cycles_twice(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    flame = Flame()
    await flame.async_initialize(hass, "sat")
    run_cycle(flame, freezer, 300)
    run_cycle(flame, freezer, 300)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    await flame.async_initialize(hass, "sat")

    assert (flame.cycles_last_hour, flame.sample_count_4h) == (2.0, 2)


async def test_the_entry_restores_its_cycle_statistics(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    now = time()
    hass_storage[f"{DOMAIN}.sat.flame"] = {
        "version": 1,
        "key": f"{DOMAIN}.sat.flame",
        "data": {
            "average_on_time_seconds": 250.0,
            "last_cycle_duration_seconds": 200.0,
            "last_cut_short_peak_flow": None,
            "last_cut_short_setpoint": None,
            "cycle_end_times": [now - 5400, now - 600],
            "on_durations": [[now - 5400, 300.0], [now - 600, 200.0]],
            "on_deltas": [],
            "pwm_burns_cut_short": [],
        },
    }
    entry = MockConfigEntry(domain=DOMAIN, entry_id="sat", data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    flame = entry.runtime_data.coordinator.flame

    assert (flame.average_on_time_seconds, flame.sample_count_4h, flame.cycles_last_hour) == (
        250.0,
        2,
        1.0,
    )
    assert flame.median_on_duration_seconds_4h == 250.0


async def test_removing_the_entry_deletes_its_cycle_statistics(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    hass_storage[f"{DOMAIN}.sat.flame"] = {
        "version": 1,
        "key": f"{DOMAIN}.sat.flame",
        "data": {"average_on_time_seconds": 250.0},
    }
    entry = MockConfigEntry(domain=DOMAIN, entry_id="sat", data=DEFAULT_USER_DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert f"{DOMAIN}.sat.flame" not in hass_storage

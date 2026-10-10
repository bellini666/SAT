"""Tests for the PID controller."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory

from custom_components.sat.const import HEATING_SYSTEM_RADIATORS
from custom_components.sat.errors import Error
from custom_components.sat.pid import PID

SENSOR = "sensor.test_inside_sensor"


def create_pid(kp: float = 0.0, ki: float = 0.0, kd: float = 0.0) -> PID:
    return PID(
        heating_system=HEATING_SYSTEM_RADIATORS,
        automatic_gain_value=1.0,
        heating_curve_coefficient=1.0,
        derivative_time_weight=1.0,
        kp=kp, ki=ki, kd=kd,
    )


def step(pid: PID, freezer: FrozenDateTimeFactory, error: float) -> None:
    freezer.tick(timedelta(seconds=30))
    pid.update(Error(SENSOR, error), heating_curve_value=40.0, boiler_temperature=50.0)


def test_derivative_decays_inside_the_deadband(freezer: FrozenDateTimeFactory) -> None:
    pid = create_pid(kd=1000.0)

    for error in (1.0, 0.6, 0.3, 0.05):
        step(pid, freezer, error)

    entry_derivative = pid.derivative
    assert entry_derivative < -1.0

    for error in (0.0, 0.05, 0.0, 0.05, 0.0, 0.05):
        step(pid, freezer, error)

    assert entry_derivative < pid.derivative <= 0.0
    assert abs(pid.derivative) < 0.05 * abs(entry_derivative)


def test_integral_stops_on_the_sample_that_leaves_the_deadband(freezer: FrozenDateTimeFactory) -> None:
    pid = create_pid(ki=0.001)

    for error in (0.3, 0.05, 0.08):
        step(pid, freezer, error)

    assert pid.integral == 0.002

    step(pid, freezer, 0.5)

    assert pid.integral == 0.0


def test_derivative_decays_on_a_steady_error_inside_the_deadband(freezer: FrozenDateTimeFactory) -> None:
    pid = create_pid(kd=1000.0)

    for error in (1.0, 0.6, 0.3, 0.05):
        step(pid, freezer, error)

    entry_derivative = pid.derivative
    assert entry_derivative < -1.0

    for _ in range(6):
        step(pid, freezer, 0.05)

    assert entry_derivative < pid.derivative <= 0.0
    assert abs(pid.derivative) < 0.05 * abs(entry_derivative)

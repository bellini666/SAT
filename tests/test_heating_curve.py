"""Tests for the heating curve."""

import pytest

from custom_components.sat.const import HEATING_SYSTEM_RADIATORS
from custom_components.sat.heating_curve import HeatingCurve


def test_autotune_skips_a_flat_heating_curve() -> None:
    heating_curve = HeatingCurve(heating_system=HEATING_SYSTEM_RADIATORS, coefficient=1.8)

    heating_curve.autotune(setpoint=40.0, target_temperature=20.0, outside_temperature=20.0)

    assert heating_curve.optimal_coefficient is None


def test_autotune_first_sample_has_no_derivative() -> None:
    heating_curve = HeatingCurve(heating_system=HEATING_SYSTEM_RADIATORS, coefficient=1.8)

    heating_curve.autotune(setpoint=40.0, target_temperature=20.0, outside_temperature=0.0)

    assert heating_curve.coefficient_derivative == 0.0
    assert heating_curve.optimal_coefficient == 2.6


@pytest.mark.parametrize(("setpoint", "derivative", "optimal_coefficient"), [
    (45.2, 1.0, 3.0),
    (35.2, -1.0, 2.2),
    (41.7, 0.3, 2.7),
    (38.7, -0.3, 2.5),
])
def test_autotune_moves_against_the_derivative(setpoint: float, derivative: float, optimal_coefficient: float) -> None:
    heating_curve = HeatingCurve(heating_system=HEATING_SYSTEM_RADIATORS, coefficient=1.8)
    heating_curve.autotune(setpoint=40.0, target_temperature=20.0, outside_temperature=0.0)

    heating_curve.autotune(setpoint=setpoint, target_temperature=20.0, outside_temperature=0.0)

    assert heating_curve.coefficient_derivative == derivative
    assert heating_curve.optimal_coefficient == optimal_coefficient

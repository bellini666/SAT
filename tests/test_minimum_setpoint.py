"""Tests for the dynamic minimum setpoint."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory

from custom_components.sat.boiler import BoilerState
from custom_components.sat.const import BoilerStatus, PWMStatus
from custom_components.sat.minimum_setpoint import MinimumSetpoint


def boiler_state(**overrides) -> BoilerState:
    values = {
        "is_active": True,
        "is_inactive": False,
        "status": BoilerStatus.AT_SETPOINT,
        "flame_active": True,
        "hot_water_active": False,
        "setpoint": 50.0,
        "flow_temperature": 50.0,
        "return_temperature": 40.0,
        "relative_modulation_level": 30.0,
    }
    values.update(overrides)
    return BoilerState(**values)


def test_missing_sensor_values_keep_the_last_minimum_setpoint() -> None:
    minimum_setpoint = MinimumSetpoint(adjustment_factor=0.2, configured_minimum_setpoint=40.0)
    minimum_setpoint.warming_up(boiler_state())
    minimum_setpoint.calculate(boiler_state(return_temperature=45.0), PWMStatus.IDLE)

    minimum_setpoint.warming_up(boiler_state(return_temperature=None))
    minimum_setpoint.calculate(boiler_state(relative_modulation_level=None), PWMStatus.IDLE)
    minimum_setpoint.calculate(boiler_state(return_temperature=None), PWMStatus.IDLE)
    minimum_setpoint.calculate(boiler_state(flow_temperature=None, return_temperature=45.0), PWMStatus.IDLE)

    assert minimum_setpoint.current == 41.0


def test_missing_values_while_modulation_is_steady_keep_the_last_minimum_setpoint(freezer: FrozenDateTimeFactory) -> None:
    minimum_setpoint = MinimumSetpoint(adjustment_factor=0.2, configured_minimum_setpoint=40.0)
    minimum_setpoint.warming_up(boiler_state())
    minimum_setpoint.calculate(boiler_state(), PWMStatus.IDLE)
    freezer.tick(timedelta(seconds=181))
    minimum_setpoint.calculate(boiler_state(), PWMStatus.IDLE)
    assert minimum_setpoint.current == 32.0

    minimum_setpoint.calculate(boiler_state(relative_modulation_level=None, return_temperature=45.0), PWMStatus.IDLE)
    minimum_setpoint.calculate(boiler_state(flow_temperature=None, return_temperature=45.0), PWMStatus.IDLE)

    assert minimum_setpoint.current == 41.0

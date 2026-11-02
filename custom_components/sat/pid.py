from __future__ import annotations

import math
import struct
from time import monotonic
from typing import TYPE_CHECKING, NamedTuple

from .const import DEADBAND, HEATING_SYSTEM_UNDERFLOOR
from .control import ControlModule

if TYPE_CHECKING:
    from homeassistant.core import State

    from .errors import Error


AUTOMATIC_GAINS_VERSION_1 = 1
AUTOMATIC_GAINS_VERSION_2 = 2
AUTOMATIC_GAINS_VERSION_3 = 3


class _Snapshot(NamedTuple):
    error: float
    previous_error: float
    updated: float
    kp: float
    ki: float
    kd: float
    proportional: float
    integral: float
    derivative: float
    raw_derivative: float
    inside_deadband: float


_LAYOUT = struct.Struct("<11d")


class PID:
    """Adapt Home Assistant room samples and restored attributes to a Rust PID."""

    def __init__(
        self,
        heating_system: str,
        automatic_gain_value: float,
        heating_curve_coefficient: float,
        derivative_time_weight: float,
        kp: float,
        ki: float,
        kd: float,
        *,
        deadband: float = DEADBAND,
        automatic_gains: bool = False,
        integral_time_limit: float = 300,
        sample_time_limit: float | None = 10,
        version: int = 3,
    ) -> None:
        if automatic_gains and version not in (
            AUTOMATIC_GAINS_VERSION_1,
            AUTOMATIC_GAINS_VERSION_2,
            AUTOMATIC_GAINS_VERSION_3,
        ):
            msg = "Invalid version"
            raise ValueError(msg)

        self._module = ControlModule()
        self._apply(
            "pid_init",
            heating_system == HEATING_SYSTEM_UNDERFLOOR,
            automatic_gain_value,
            heating_curve_coefficient,
            derivative_time_weight,
            kp,
            ki,
            kd,
            deadband,
            automatic_gains,
            max(integral_time_limit, 1),
            max(sample_time_limit, 1) if sample_time_limit is not None else 0,
            version,
        )

    def _apply(self, name: str, *args: float) -> None:
        self._state = _Snapshot(*self._module.record(_LAYOUT, name, monotonic(), *args))

    def reset(self) -> None:
        self._apply("pid_reset", 0, 0)

    def update(self, error: Error, heating_curve_value: float) -> None:
        self._apply("pid_update", error.value, heating_curve_value)

    def update_reset(self, error: Error, heating_curve_value: float | None) -> None:
        self._apply("pid_reset", error.value, heating_curve_value or 0.0)

    def update_integral(
        self, error: Error, heating_curve_value: float, *, force: bool = False
    ) -> None:
        self._apply("pid_integral", error.value, heating_curve_value, force)

    def update_derivative(self, error: Error, alpha1: float = 0.8, alpha2: float = 0.6) -> None:
        self._apply("pid_derivative", error.value, alpha1, alpha2)

    def restore(self, state: State) -> None:
        def saved(key: str) -> float:
            value = state.attributes.get(key)
            return float(value) if value is not None else math.nan

        self._apply("pid_restore", saved("error"), saved("integral"), saved("heating_curve"))

    @property
    def last_error(self) -> float:
        return self._state.error

    @property
    def previous_error(self) -> float:
        return self._state.previous_error

    @property
    def last_updated(self) -> float:
        return self._state.updated

    @property
    def kp(self) -> float:
        return self._state.kp

    @property
    def ki(self) -> float:
        return self._state.ki

    @property
    def kd(self) -> float:
        return self._state.kd

    @property
    def proportional(self) -> float:
        return self._state.proportional

    @property
    def integral(self) -> float:
        return self._state.integral

    @property
    def derivative(self) -> float:
        return self._state.derivative

    @property
    def raw_derivative(self) -> float:
        return self._state.raw_derivative

    @property
    def output(self) -> float:
        """Return the control output value."""
        return self.proportional + self.integral + self.derivative

    @property
    def integral_enabled(self) -> bool:
        """Return whether the updates of the integral are enabled."""
        return bool(self._state.inside_deadband)

    @property
    def derivative_enabled(self) -> bool:
        """Return whether the updates of the derivative are enabled."""
        return not self.integral_enabled

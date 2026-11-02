from collections import deque
from statistics import mean

from . import control
from .const import (
    HEATING_SYSTEM_UNDERFLOOR,
    MINIMUM_SETPOINT,
)


class HeatingCurve:
    def __init__(self, heating_system: str, coefficient: float) -> None:
        """Initialize the heating curve.

        :param heating_system: The type of heating system, either "underfloor" or "radiator"
        :param coefficient: The coefficient used to adjust the heating curve
        """
        self._coefficient = coefficient
        self._heating_system = heating_system
        self.reset()

    def reset(self) -> None:
        self._optimal_coefficient = None
        self._coefficient_derivative = None
        self._last_heating_curve_value = None
        self._optimal_coefficients = deque(maxlen=5)

    def update(self, target_temperature: float, outside_temperature: float) -> None:
        """Calculate the heating curve based on the outside temperature."""
        heating_curve_value = self.calculate(target_temperature, outside_temperature)
        self._last_heating_curve_value = control.value(
            "curve_setpoint", self.base_offset, self._coefficient, heating_curve_value
        )

    def calculate_coefficient(
        self, setpoint: float, target_temperature: float, outside_temperature: float
    ) -> float:
        """Convert a setpoint to a coefficient value."""
        heating_curve_value = self.calculate(target_temperature, outside_temperature)
        if heating_curve_value == 0:
            msg = "float division by zero"
            raise ZeroDivisionError(msg)
        return control.value("curve_coefficient", self.base_offset, setpoint, heating_curve_value)

    def autotune(
        self, setpoint: float, target_temperature: float, outside_temperature: float
    ) -> None:
        """Calculate an optimal coefficient value."""
        if (
            setpoint <= MINIMUM_SETPOINT
            or self.calculate(target_temperature, outside_temperature) <= 0
        ):
            return

        coefficient = self.calculate_coefficient(setpoint, target_temperature, outside_temperature)
        self._coefficient_derivative = (
            round(coefficient - self._optimal_coefficient, 1)
            if self._optimal_coefficient is not None
            else 0.0
        )

        coefficient = control.value("autotune_sample", coefficient, self._coefficient_derivative)

        # Store the results
        self._optimal_coefficients.append(coefficient)
        self._optimal_coefficient = round(mean(self._optimal_coefficients), 1)

    def restore_autotune(self, coefficient: float, derivative: float | None) -> None:
        """Restore a previous optimal coefficient value."""
        self._optimal_coefficient = coefficient
        self._coefficient_derivative = derivative

        self._optimal_coefficients = deque([coefficient] * 5, maxlen=5)

    @staticmethod
    def calculate(target_temperature: float, outside_temperature: float) -> float:
        """Calculate the heating curve value based on the current outside temperature."""
        return control.value("heating_curve", target_temperature, outside_temperature)

    @property
    def base_offset(self) -> float:
        """Determine the base offset for the heating system."""
        # Heat pumps use the radiator offset:
        # upstream's Precision Curve only defines radiators (27.2) and underfloor (20)
        return 20 if self._heating_system == HEATING_SYSTEM_UNDERFLOOR else 27.2

    @property
    def optimal_coefficient(self) -> float | None:
        return self._optimal_coefficient

    @property
    def coefficient_derivative(self) -> float | None:
        return self._coefficient_derivative

    @property
    def value(self) -> float | None:
        return self._last_heating_curve_value

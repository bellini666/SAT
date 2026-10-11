from homeassistant.const import UnitOfTemperature
from homeassistant.util.unit_conversion import TemperatureConverter

BASE_FAHRENHEIT = 58.0

PERCEPTION_THRESHOLDS = (
    (21.1, "Cool"),
    (25.0, "Slightly Cool"),
    (28.3, "Comfortable"),
    (32.8, "Slightly Warm"),
    (37.8, "Increasing Discomfort"),
    (44.4, "Extremely Warm"),
    (51.7, "Danger Of Heatstroke"),
    (65.6, "Extreme Danger Of Heatstroke"),
)


class SummerSimmer:
    @staticmethod
    def index(temperature: float | None, humidity: float | None) -> float | None:
        """Calculate the Summer Simmer Index.

        The Summer Simmer Index is a measure of heat and humidity.

        Formula: 1.98 * (F - (0.55 - 0.0055 * H) * (F - 58.0)) - 56.83
        If F < 58, the index is F.

        Returns:
            float: Summer Simmer Index in Celsius.

        """
        # Make sure we have a valid value
        if temperature is None or humidity is None:
            return None

        # Convert temperature to Fahrenheit
        fahrenheit = TemperatureConverter.convert(
            temperature, UnitOfTemperature.CELSIUS, UnitOfTemperature.FAHRENHEIT
        )

        # Calculate Summer Simmer Index
        index = (
            1.98 * (fahrenheit - (0.55 - 0.0055 * humidity) * (fahrenheit - BASE_FAHRENHEIT))
            - 56.83
        )

        # If the temperature is below 58°F, use the temperature as the index
        if fahrenheit < BASE_FAHRENHEIT:
            index = fahrenheit

        # Convert the result back to Celsius
        return round(
            TemperatureConverter.convert(
                index, UnitOfTemperature.FAHRENHEIT, UnitOfTemperature.CELSIUS
            ),
            1,
        )

    @staticmethod
    def perception(temperature: float | None, humidity: float | None) -> str:
        index = SummerSimmer.index(temperature, humidity)

        if index is None:
            return "Unknown"

        for limit, label in PERCEPTION_THRESHOLDS:
            if index < limit:
                return label

        return "Circulatory Collapse Imminent"

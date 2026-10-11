import pytest

from custom_components.sat.helpers import convert_time_str_to_seconds


def test_convert_time_str_to_seconds_rejects_invalid_input() -> None:
    with pytest.raises(ValueError, match="Invalid time format"):
        convert_time_str_to_seconds("not a time")

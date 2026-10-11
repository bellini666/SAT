from typing import override

from ..manufacturer import Manufacturer


class Geminox(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Geminox"

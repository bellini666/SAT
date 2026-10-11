from typing import override

from ..manufacturer import Manufacturer


class Nefit(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Nefit"

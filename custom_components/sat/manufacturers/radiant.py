from typing import override

from ..manufacturer import Manufacturer


class Radiant(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Radiant"

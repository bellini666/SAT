from typing import override

from ..manufacturer import Manufacturer


class Baxi(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Baxi"

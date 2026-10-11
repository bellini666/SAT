from typing import override

from ..manufacturer import Manufacturer


class Vaillant(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Vaillant"

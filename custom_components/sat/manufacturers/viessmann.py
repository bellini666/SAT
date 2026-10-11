from typing import override

from ..manufacturer import Manufacturer


class Viessmann(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Viessmann"

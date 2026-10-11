from typing import override

from ..manufacturer import Manufacturer


class Intergas(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Intergas"

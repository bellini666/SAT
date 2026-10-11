from typing import override

from ..manufacturer import Manufacturer


class Remeha(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Remeha"

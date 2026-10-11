from typing import override

from ..manufacturer import Manufacturer


class Immergas(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Immergas"

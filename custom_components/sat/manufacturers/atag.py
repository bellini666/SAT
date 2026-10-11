from typing import override

from ..manufacturer import Manufacturer


class Atag(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "ATAG"

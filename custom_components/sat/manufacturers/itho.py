from typing import override

from ..manufacturer import Manufacturer


class Itho(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Itho"

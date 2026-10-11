from typing import override

from ..manufacturer import Manufacturer


class Brotge(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "BRÖTGE"

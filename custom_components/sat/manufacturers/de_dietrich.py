from typing import override

from ..manufacturer import Manufacturer


class DeDietrich(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "De Dietrich"

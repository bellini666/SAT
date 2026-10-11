from typing import override

from ..manufacturer import Manufacturer


class Worcester(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Worcester Bosch"

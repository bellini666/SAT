from typing import override

from ..manufacturer import Manufacturer


class Other(Manufacturer):
    @override
    @property
    def friendly_name(self) -> str:
        return "Other"

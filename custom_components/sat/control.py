"""Load the same Rust control module on every HACS platform."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import pywasm

if TYPE_CHECKING:
    import struct

# Home Assistant imports integrations in its executor. Parse once there, then instantiate
# from memory so creating a room controller never opens a file on the event loop.
with Path(__file__).with_name("control.wasm").open("rb") as _source:
    _MODULE = pywasm.ModuleDesc.from_reader(_source)


def _decimal_round(_machine: pywasm.Machine, args: list[float]) -> list[float]:
    return [round(args[0], int(args[1]))]


def optional(value: float | None) -> float:
    """Represent an absent measurement at the numeric WASM boundary."""
    return math.nan if value is None else value


class ControlModule:
    """Own one WASM instance. A PID's memory is private to its room."""

    def __init__(self) -> None:
        self.runtime = pywasm.Runtime()
        signature = pywasm.FuncType(
            [pywasm.ValType.f64(), pywasm.ValType.i32()], [pywasm.ValType.f64()]
        )
        self.runtime.imports["sat"] = {
            "decimal_round": self.runtime.allocate_func_host(signature, _decimal_round)
        }
        self.module = self.runtime.instance(_MODULE)
        self.memory = self.runtime.exported_memory(self.module, "memory")

    def call(self, name: str, *args: float) -> float:
        return float(self.runtime.invocate(self.module, name, list(args))[0])

    def record(self, layout: struct.Struct, name: str, *args: float) -> tuple[float, ...]:
        address = int(self.call(name, *args))
        return tuple(map(float, layout.unpack_from(self.memory.data, address)))


# Calls are synchronous on Home Assistant's event loop.
shared = ControlModule()
value = shared.call

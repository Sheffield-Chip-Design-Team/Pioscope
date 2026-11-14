"""Net resolution for the board around the DUT.

Drive values: 0, 1, Z (released) or X (unknown, e.g. an X from RTL).
Resolution of one net from all its drivers:

* any X drive                -> X
* both 0 and 1 driven        -> contention (BusConflict) -> X
* only 0s / only 1s          -> that level
* nothing driven             -> pull level, or X if the net has no pull

Open-drain buses therefore behave as wired-AND with a pull-up, as long as
open-drain drivers only ever drive 0 or Z (devices do; the DUT does so via OE).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from sim.wave import Drive, Level, X, Z

__all__ = ["BusConflict", "Drive", "Level", "X", "Z", "resolve"]


@dataclass(frozen=True)
class BusConflict(Exception):
    net: str
    cycle: int
    drivers: tuple[tuple[str, Drive], ...]

    def __str__(self) -> str:
        who = ", ".join(f"{d}={'01XZ'[v]}" for d, v in self.drivers)
        return f"bus conflict on net '{self.net}' at cycle {self.cycle}: {who}"


def resolve(drives: Iterable[tuple[str, Drive]], pull: int | None) -> tuple[Level, bool]:
    """Return (level, conflict)."""
    seen0 = seen1 = False
    for _, d in drives:
        if d == X:
            return X, False
        if d == 0:
            seen0 = True
        elif d == 1:
            seen1 = True
    if seen0 and seen1:
        return X, True
    if seen0:
        return 0, False
    if seen1:
        return 1, False
    return (pull if pull is not None else X), False

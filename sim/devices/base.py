"""Device models: the other end of each bus.

A device is a synchronous step machine on sysclk. It is NOT a cocotb coroutine,
so the same object runs on the pure-Python backend and on the cocotb RTL
backend. Each cycle the bench calls ``step(cycle, view)`` where `view` maps the
device's ports to resolved net levels; the device returns its drives
(0, 1, Z = released, per port). Drives take effect on the bus in the same
cycle, after every device has stepped (see ADR-0002).

Devices derive real time from ``cycle / sysclk_hz``, which is how clock error
(ppm) is injected. Randomness only through ``self.rng`` (seeded).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, field
from typing import Any

from sim.wave import Drive, Level, Z

from .clock import DeviceClock


@dataclass(frozen=True)
class At:
    """Script wait: resume at absolute time `t` (seconds). Drives made after
    resuming are stamped with `t`, i.e. they happen part-way through a DUT cycle."""

    t: float


# What a script may yield: a number of DUT cycles to wait (>= 1), a predicate
# that is re-checked every following cycle until true, or At(t).
Wait = int | Callable[[], bool] | At
Script = Generator[Wait, None, None]

_EPS = 1e-9  # cycles; guards float rounding when converting times to cycles


@dataclass
class Txn:
    """One logged transaction (byte received, ACK seen, error, ...)."""

    cycle: int
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    #: exact time in seconds (sub-cycle for events on a device's own clock)
    t: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"cycle": self.cycle, "kind": self.kind, "data": self.data, "t": self.t}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Txn:
        return cls(int(d["cycle"]), str(d["kind"]), dict(d["data"]), float(d.get("t", 0.0)))


class Device:
    ports: tuple[str, ...] = ()

    def __init__(self, name: str, seed: int = 0) -> None:
        self.name = name
        self.seed = seed
        self.rng = random.Random(seed)
        self.log: list[Txn] = []
        self.sysclk_hz = 0.0
        self.cycle = 0
        self._view: Mapping[str, Level] = {}
        self._drives: dict[str, Drive] = {p: Z for p in self.ports}
        # port -> time (s) of this cycle's change; None = start of cycle
        self.change_times: dict[str, float | None] = {}
        self.pulses_lost = 0  # extra changes squeezed into one DUT cycle
        self._t: float | None = None  # stamp for drives without explicit t

    # ---- bench interface ------------------------------------------------------
    def attach(self, sysclk_hz: float) -> None:
        self.sysclk_hz = sysclk_hz
        self.on_attach()

    def step(self, cycle: int, view: Mapping[str, Level]) -> Mapping[str, Drive]:
        self.cycle = cycle
        self._view = view
        self.change_times = {}
        self.tick()
        self._t = None
        return self._drives

    # ---- for subclasses -------------------------------------------------------
    def on_attach(self) -> None:
        """Called once sysclk_hz is known, before cycle 0."""

    def tick(self) -> None:
        """Per-cycle behaviour."""

    def read(self, port: str) -> Level:
        return self._view.get(port, Z)

    def drive(self, port: str, value: Drive, t: float | None = None) -> None:
        """Drive `port`. `t` is the absolute time (s) of the change; it must lie in
        the current DUT cycle. Default: the current script time, else cycle start."""
        if port not in self._drives:
            raise KeyError(f"{self.name}: no port '{port}'")
        if self._drives[port] == value:
            return
        if t is None:
            t = self._t
        if t is not None and self.sysclk_hz:
            c = self.cycle_of(t)
            if c != self.cycle:
                raise ValueError(f"{self.name}: drive at t={t:.3e}s is in cycle {c}, "
                                 f"not the current cycle {self.cycle}")
        if port in self.change_times:
            self.pulses_lost += 1
        self.change_times[port] = t
        self._drives[port] = value

    def release(self, port: str) -> None:
        self.drive(port, Z)

    def emit(self, kind: str, at: float | None = None, **data: Any) -> None:
        """Log a transaction. `at` overrides the timestamp (exact event time, s)."""
        t = at if at is not None else (self.now() if self.sysclk_hz else 0.0)
        self.log.append(Txn(self.cycle, kind, data, t))

    def clocks(self) -> dict[str, tuple[DeviceClock, str]]:
        """The device's own clocks, for display: name -> (clock, style). Style 'tick'
        draws a pulse at every tick (e.g. UART bit boundaries); 'toggle' flips the
        level at every tick (a free-running version of a bus clock)."""
        return {}

    def seconds_to_cycles(self, s: float) -> float:
        return s * self.sysclk_hz

    def now(self) -> float:
        """Current time (s): the script time if set, else the start of this cycle."""
        return self._t if self._t is not None else self.cycle / self.sysclk_hz

    def cycle_of(self, t: float) -> int:
        return math.floor(t * self.sysclk_hz + _EPS)


class ScriptedDevice(Device):
    """A device whose behaviour is a sequential generator (good for bus masters)."""

    def __init__(self, name: str, seed: int = 0) -> None:
        super().__init__(name, seed)
        self._gen: Script | None = None
        self._wait_cycles = 0
        self._wait_pred: Callable[[], bool] | None = None
        self._wait_at: At | None = None
        self.done = False

    def script(self) -> Script:
        return
        yield  # pragma: no cover

    def restart(self, gen: Script | None = None) -> None:
        self._gen = gen if gen is not None else self.script()
        self._wait_cycles = 0
        self._wait_pred = None
        self._wait_at = None
        self.done = False

    def tick(self) -> None:
        if self._gen is None:
            if self.done:
                return
            self.restart()
        if self._wait_pred is not None:
            if not self._wait_pred():
                return
            self._wait_pred = None
        elif self._wait_at is not None:
            if self.cycle < self.cycle_of(self._wait_at.t):
                return
            self._t = self._wait_at.t
            self._wait_at = None
        elif self._wait_cycles > 0:
            self._wait_cycles -= 1
            if self._wait_cycles > 0:
                return
        self._resume()

    def _resume(self) -> None:
        assert self._gen is not None
        # An At() that falls inside the current cycle resumes immediately (the
        # device clock is faster than sysclk/2); bounded so a bad script cannot hang.
        for _ in range(100_000):
            try:
                w = next(self._gen)
            except StopIteration:
                self._gen = None
                self.done = True
                return
            if isinstance(w, At):
                if self.cycle_of(w.t) <= self.cycle:
                    self._t = max(w.t, self.cycle / self.sysclk_hz)
                    continue
                self._wait_at = w
            elif callable(w):
                self._wait_pred = w
            else:
                if w < 1:
                    raise ValueError(f"{self.name}: scripts must wait >= 1 cycle, got {w}")
                self._wait_cycles = w
            return
        raise RuntimeError(f"{self.name}: script made no progress within one cycle")

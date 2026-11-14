"""The generic engine: the DUT contract (pioscope/dut/base.py) over a list of cores.

A model subclasses `Engine` and overrides `build()` to fill two lists: `cores`
(cores, `pioscope.dut.core.Core` subclasses) and `shared` (units shared between cores).
The observation methods aggregate over both (ADR-0010):

- `drain_events()`: trace events as (source, event), the source being the core's name
  or `shared`;
- `probes()` / `probe_bits()`: integer signals recorded into the VCD on change, named
  `<core>.<probe>` and `shared.<unit>.<probe>`;
- `state()`: a nested snapshot for the debugger (`pioscope debug`, VS Code).

So a model with more cores or shared units gets the debugger, the trace and the VCD
for free. See docs/reference/simulator.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pioscope.bench.pads import PadIn, PadOut
from pioscope.devices.base import Txn
from pioscope.image import ProgramImage

from .config import SYSCLK_HZ
from .core import Core
from .errors import EngineError
from .sync import InputSync
from .unit import Unit


class Engine:
    """The DUT contract over a list of cores and shared units (see the module doc)."""

    is_engine = True

    def __init__(self, sysclk_hz: int = SYSCLK_HZ) -> None:
        self.fs = sysclk_hz
        self.image: ProgramImage | None = None
        self.cores: list[Core] = []
        self.shared: list[Unit] = []
        self.shared_events: list[Txn] = []
        self.sync = InputSync(16)
        self.cycle = 0

    # ---- building ---------------------------------------------------------------------
    def build(self, image: ProgramImage) -> None:
        """Create the cores and shared units for `image` (every model overrides
        this: it fills `self.cores` and `self.shared`)."""
        raise NotImplementedError(f"{type(self).__name__}.build")

    def _build(self) -> None:
        if self.image is None:
            raise EngineError("no program loaded")
        self.build(self.image)
        self.shared_events = []
        self.sync = InputSync(16)
        self.cycle = 0

    # ---- DUT contract -----------------------------------------------------------------
    def load(self, image: ProgramImage) -> None:
        self.image = image
        self._build()

    def reset(self) -> PadOut:
        self._build()
        return self._pads()

    def step(self, pins_in: PadIn) -> PadOut:
        levels = [(pins_in.ui >> i) & 1 for i in range(8)] + [
            (pins_in.uio_in >> i) & 1 for i in range(8)]
        seen = self.sync.step(levels)
        for t in self._live:
            t.pads_seen = list(seen)
            t.sysclk()
        for u in self.shared:
            u.on_cycle(self)
        self.cycle += 1
        return self._pads()

    def host_push(self, tc: int, word: int) -> bool:
        return 0 <= tc < len(self._live) and self._live[tc].txf.push(word)

    def host_pop(self, tc: int) -> int | None:
        return self._live[tc].rxf.pop() if 0 <= tc < len(self._live) else None

    def host_irq(self) -> int:
        irq = 0
        for t in self._live:
            irq |= t.irq
        return irq

    # ---- observation (ADR-0010) -------------------------------------------------------
    def probes(self) -> Mapping[str, int]:
        out: dict[str, int] = {}
        for t in self._live:
            out.update({f"{t.name}.{k}": v for k, v in t.probes().items()})
        for u in self.shared:
            out.update({f"shared.{u.name}.{k}": v for k, v in u.probes().items()})
        return out

    def probe_bits(self) -> dict[str, int]:
        bits: dict[str, int] = {}
        for t in self.cores:
            bits.update({f"{t.name}.{k}": v for k, v in t.probe_bits().items()})
        for u in self.shared:
            bits.update({f"shared.{u.name}.{k}": v for k, v in u.probe_bits().items()})
        return bits

    def drain_events(self) -> list[tuple[str, Txn]]:
        out: list[tuple[str, Txn]] = []
        for t in self._live:
            out += [(t.name, e) for e in t.events]
            t.events = []
        out += [("shared", e) for e in self.shared_events]
        self.shared_events = []
        return out

    def emit_shared(self, kind: str, **data: Any) -> None:
        """For shared units: record a trace event under the source `shared`."""
        self.shared_events.append(Txn(self.cycle, kind, data, self.cycle / self.fs))

    def state(self) -> dict[str, Any]:
        """Everything the debugger shows: the cycle, each core's state, and the
        shared units' state."""
        return {
            "cycle": self.cycle, "time_s": self.cycle / self.fs,
            "cores": {t.name: t.state() for t in self._live},
            "shared": {u.name: u.state() for u in self.shared},
        }

    @property
    def _live(self) -> list[Core]:
        if not self.cores:
            raise EngineError("no program loaded: the engine needs an image")
        return self.cores

    def _pads(self) -> PadOut:
        """The pads driven by the cores' output roles. A pad is driven by the first
        core with `drives_pads` that maps an output role to it."""
        uo = uio_out = uio_oe = 0
        owned: set[tuple[str, int]] = set()
        for t in self._live:
            if not t.drives_pads:
                continue
            for i, r in enumerate(t.cfg.outputs):
                if (r.group, r.index) in owned:
                    continue
                owned.add((r.group, r.index))
                if r.group == "uo":
                    uo |= t.out_level[i] << r.index
                else:
                    uio_out |= t.out_level[i] << r.index
                    uio_oe |= int(t.out_oe[i]) << r.index
        return PadOut(uo=uo, uio_out=uio_out, uio_oe=uio_oe)

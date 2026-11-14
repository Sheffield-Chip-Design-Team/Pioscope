"""The DUT contract. Anything that implements it can sit in the DUT slot:
the empty stub now, a Python engine model later, and (through the cocotb
backend) the RTL.

Timing contract (docs/open_items/0002-cycle-phase-order.md):
``reset()`` returns the outputs while the design is held in reset and just
released; ``step(pins_in)`` applies `pins_in` at one rising clock edge and
returns the outputs registered by that edge.

Host port: the engine's CPU-side interface (TX/RX FIFOs, IRQ flags) is not yet
designed. Scenarios use it through these methods; each DUT maps them to
whatever it has (the RTL backend will use a backdoor adapter). `tc` selects the
core whose FIFO is meant.

Observability (optional, ADR-0010): a model that also provides `cores`,
`state()`, `drain_events()` returning (source, event) pairs and dotted `probes()`
names is traced, recorded into the VCD and debugged (`pioscope debug --dut ...`)
without any change to the tools. `pioscope.dut.engine.Engine` is the reference; see
docs/reference/simulator.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pioscope.bench.pads import PadIn, PadOut
from pioscope.image import ProgramImage


@runtime_checkable
class Dut(Protocol):
    #: False for stand-ins that cannot run protocol programs.
    is_engine: bool

    def load(self, image: ProgramImage) -> None: ...

    def reset(self) -> PadOut: ...

    def step(self, pins_in: PadIn) -> PadOut: ...

    # ---- host port -----------------------------------------------------------
    def host_push(self, tc: int, word: int) -> bool:
        """Offer a word to core `tc`'s TX FIFO. Returns False if it is full."""
        ...

    def host_pop(self, tc: int) -> int | None:
        """Take a word from core `tc`'s RX FIFO, or None if empty."""
        ...

    def host_irq(self) -> int:
        """Bitmask of raised IRQ flags."""
        ...

    def probes(self) -> Mapping[str, int]:
        """Optional internal state for metrics/tracing (pc, stall, ...)."""
        ...

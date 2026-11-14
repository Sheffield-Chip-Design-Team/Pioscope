"""Tutorials 03 and 04: a toy unit, and an engine that carries it.

`EdgeCounter` watches the first output role of the core it is attached to and counts
its edges. It is observe-only (no instruction uses it), which makes it the smallest
possible unit: a `name`, `on_cycle`, `state` and `probes`. It reads the pins through
the core's `pins_state()`, so it works on any model.

`EdgeCountingEngine` is the PIO model with the unit appended to sm0's `units` list.
That is all it takes: the unit appears in VS Code (Variables > Units), in
`info units`, and in the VCD as engine.sm0.units.edge_counter.edges.

Run it:  uv run pioscope debug --scenario pioscope.examples.pulse_counter:blink_scenario \
             --dut pioscope.examples.edge_counter:EdgeCountingEngine
         (pioscope) stepi 1000
         (pioscope) info units edge_counter
"""

from __future__ import annotations

from typing import Any

from pioscope.dut.core import Unit
from pioscope.dut.pio import PioEngine
from pioscope.image import ProgramImage


class EdgeCounter(Unit):
    name = "edge_counter"

    def __init__(self) -> None:
        self.last: int | None = None
        self.edges = 0

    def on_cycle(self, owner: Any) -> None:
        """Called once per sysclk, after the core's own work. `owner` is the core."""
        outs = owner.pins_state()["out"]
        if not outs:
            return
        level = next(iter(outs.values()))["level"]
        if self.last is not None and level != self.last:
            self.edges += 1
        self.last = level

    def state(self) -> dict[str, Any]:
        return {"edges": self.edges, "level": self.last}

    def probes(self) -> dict[str, int]:
        return {"edges": self.edges}

    def probe_bits(self) -> dict[str, int]:
        return {"edges": 16}


class EdgeCountingEngine(PioEngine):
    def build(self, image: ProgramImage) -> None:
        super().build(image)  # one state machine, sm0
        self.cores[0].units.append(EdgeCounter())

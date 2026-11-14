"""The interface every unit implements, so the tools can see it (ADR-0010).

A unit is a piece of hardware attached to a core (a timer, a CRC generator, ...) or shared
between cores (an arbiter, a pooled CRC unit, a reconfiguration controller).
The debugger, the trace and the VCD never name a unit: they iterate over
`Core.units` and `Engine.shared` and ask each one for:

- `state()`: everything worth seeing, as plain data (dicts, lists, ints, bools, str).
  It appears in VS Code's Variables view under Units and in kdb's `info units`.
- `probes()`: integer signals recorded into the VCD on change, as
  `engine.<core>.units.<name>.<probe>`; `probe_bits()` gives their widths.

So adding a unit means: subclass `Unit`, give it a unique `name`, and add an instance
to a core's `units` list (or the engine's `shared` list). Nothing else changes.
`on_cycle()` is called once per sysclk after the core's own work, for units that
watch the pins or count something every cycle; most units are driven by
instructions instead and leave it empty.
"""

from __future__ import annotations

from typing import Any, ClassVar


class Unit:
    #: Unique within its core (or within `Engine.shared`); used in paths and the VCD.
    name: ClassVar[str] = "unit"

    def state(self) -> dict[str, Any]:
        return {}

    def probes(self) -> dict[str, int]:
        return {}

    def probe_bits(self) -> dict[str, int]:
        """Width of each probe in bits; a probe not listed is 32 bits."""
        return {}

    def on_cycle(self, owner: Any) -> None:
        """Once per sysclk, after the owner's work this cycle (`owner` is the core,
        or the engine for a shared unit)."""

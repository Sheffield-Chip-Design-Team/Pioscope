"""What each program needs from the hardware, and the protocol mixes (workloads).

Static analysis of an assembled ProgramImage. What is ISA-specific (which units an
instruction uses, how busy they are, the bit rate) comes from the program's ISA pack
(its `demand` module: `units_used`, `bit_shares`, `fifos`, `bit_rate`; ADR-0012).
The `activity` figures are ESTIMATES (traffic load x the share of bit-times a unit
is busy). Phase 2 of the study replaces them with counters measured on the engine model
(docs/open_items/0007-architecture-study.md).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from sim.asm import assemble_file
from sim.bench.pads import PAD_NAMES
from sim.image import ProgramImage
from sim.isa import default_isa

#: Pads on the Tiny Tapeout tile.
TOTAL_PADS = len(PAD_NAMES)


@cache
def _unit_types(cost_path: Path) -> tuple[str, ...]:
    doc = yaml.safe_load(cost_path.read_text()) or {}
    return tuple(str(u) for u in (doc.get("units") or {}))


def unit_types() -> tuple[str, ...]:
    """The unit types of the active ISA pack: the keys of `units:` in its cost.yaml, in
    order (chain order for a pack with a unit chain). Empty for a pack without units."""
    from sim.packs import active

    return _unit_types(active().arch / "cost.yaml")


def __getattr__(name: str) -> Any:
    if name == "UNIT_TYPES":  # the active pack's unit types (see unit_types())
        return unit_types()
    raise AttributeError(name)


def _hooks(image: ProgramImage) -> Any:
    from sim.packs import for_image

    return for_image(image).demand_module()


def units_used(image: ProgramImage) -> list[str]:
    """The units a program needs (its ISA pack's `units_used` hook)."""
    fn = getattr(_hooks(image), "units_used", None)
    return sorted(fn(image)) if fn else []


def _bit_shares(image: ProgramImage, units: frozenset[str]) -> dict[str, float]:
    """Share of one frame's bit-times each used unit is busy (the pack's hook)."""
    fn = getattr(_hooks(image), "bit_shares", None)
    return dict(fn(image, units)) if fn else dict.fromkeys(sorted(units), 1.0)


def _fifos(image: ProgramImage) -> tuple[bool, bool]:
    """Whether the program uses its TX FIFO (host -> pins) and its RX FIFO: the pack's
    hook, else any pull/out and push/in."""
    fn = getattr(_hooks(image), "fifos", None)
    if fn:
        tx, rx = fn(image)
        return bool(tx), bool(rx)
    names = {i.name for i in image.instructions}
    return bool(names & {"pull", "out"}), bool(names & {"push", "in"})


def _bit_rate(image: ProgramImage) -> float | None:
    fn = getattr(_hooks(image), "bit_rate", None)
    return fn(image) if fn else None


@dataclass(frozen=True)
class ResourceDemand:
    program: str
    imem_words: int
    units: frozenset[str]
    fifo_tx: int  # FIFO depth needed; 0 = no FIFO in that direction
    fifo_rx: int
    pads: int
    bit_rate: float | None  # from the pack's bit_rate hook; None if not known statically
    bit_share: Mapping[str, float]  # unit -> share of bit-times busy (static)

    @property
    def fifos(self) -> int:
        return int(self.fifo_tx > 0) + int(self.fifo_rx > 0)

    def banks(self, bank_words: int) -> int:
        return math.ceil(self.imem_words / bank_words)

    def activity(self, load: float) -> dict[str, float]:
        """ESTIMATED fraction of cycles each resource does useful work, at `load`.

        The core is counted busy while its line is busy (it stalls on wait/pull
        otherwise); each unit for its share of the busy bit-times.
        """
        act = {"core": load}
        act.update({u: load * s for u, s in self.bit_share.items()})
        return act


def demand_of(image: ProgramImage) -> ResourceDemand:
    depth = int(default_isa().params["fifo_depth"])
    fifo = image.config.get("fifo") or {"tx": depth, "rx": depth}
    tx, rx = _fifos(image)
    units = frozenset(units_used(image))
    return ResourceDemand(
        program=image.name,
        imem_words=image.size,
        units=units,
        fifo_tx=int(fifo["tx"]) if tx else 0,
        fifo_rx=int(fifo["rx"]) if rx else 0,
        pads=len(image.config.get("pins") or {}),
        bit_rate=_bit_rate(image),
        bit_share=_bit_shares(image, units),
    )


def program_demand(name: str) -> ResourceDemand:
    """Demand of the active ISA pack's program `name`."""
    from sim.packs import active

    return _program_demand(active().program(name))


@cache
def _program_demand(path: Path) -> ResourceDemand:
    return demand_of(assemble_file(path))


# ---- workloads ------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """One program running in a mix, at a traffic load."""

    demand: ResourceDemand
    load: float

    @property
    def program(self) -> str:
        return self.demand.program


@dataclass(frozen=True)
class Workload:
    name: str
    tasks: tuple[Task, ...]
    description: str = ""

    @property
    def pads(self) -> int:
        return sum(t.demand.pads for t in self.tasks)


def default_workloads() -> Path:
    from sim.packs import active

    return active().arch / "workloads.yaml"


def workloads_from_dict(doc: Mapping[str, Any]) -> list[Workload]:
    out = []
    for w in doc["workloads"]:
        tasks = []
        for p in w["programs"]:
            load = float(p["load"])
            if not 0.0 <= load <= 1.0:
                raise ValueError(f"workload {w['name']}: load {load} of {p['prog']} not in 0..1")
            tasks.append(Task(program_demand(p["prog"]), load))
        out.append(Workload(w["name"], tuple(tasks), w.get("description", "")))
    return out


def load_workloads(path: Path | str | None = None) -> list[Workload]:
    p = Path(path) if path is not None else default_workloads()
    return workloads_from_dict(yaml.safe_load(p.read_text()))

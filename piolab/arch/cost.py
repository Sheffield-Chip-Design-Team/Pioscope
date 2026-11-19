"""Area model in gate equivalents (GE). Constants: the pack's arch/cost.yaml (placeholders).

`area(arch, cost)` returns a breakdown by resource class:
  core, imem (storage), imem_read (read-port muxes), fifo, unit:<type>,
  xbar (core<->unit and core<->FIFO switching), config (allocation-select flops).
`xbar`, `config` and `imem_read` are pure overhead; the others can do useful work.

imem is flops (cost per bit, plus read muxes) or SRAM macros (`imem_tech="sram"`):
the cheapest arrangement of catalogue macros that covers the words, the 32-bit
word and the read ports, converted to GE with `nand2_um2`. A macro decodes its
own address, so a private SRAM imem has no read-mux cost.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from piolab.isa import default_isa

from .arch import Arch, MeshArch, PioArch
from .demand import unit_types


def default_cost() -> Path:
    from piolab.packs import active

    return active().arch / "cost.yaml"

OVERHEAD = ("imem_read", "xbar", "config")


class Unbuildable(ValueError):
    """The architecture cannot be built from the catalogue (e.g. too many read ports)."""


@dataclass(frozen=True)
class Macro:
    name: str
    words: int
    bits: int
    ports: int
    um2: float


@dataclass(frozen=True)
class Cost:
    flop_bit: float
    mux2_bit: float
    core: float
    fifo_control: float
    unit_port_bits: float
    config_bit: float
    units: Mapping[str, float]
    word_bits: int = 32  # an instruction word (imem)
    nand2_um2: float = 1.0
    sram: tuple[Macro, ...] = ()
    tile_um2: float = 0.0
    fifo_bits: int = 32  # a FIFO entry (the ISA's `fifo_bits`, else its word)

    def tiles(self, ge: float) -> float:
        """Tiny Tapeout tiles covered by `ge` of cells and macros at 100% density."""
        return ge * self.nand2_um2 / self.tile_um2 if self.tile_um2 else 0.0

    def unit(self, u: str) -> float:
        return float(self.units[u])

    def fifo(self, depth: int) -> float:
        return depth * self.fifo_bits * self.flop_bit + self.fifo_control

    def word(self) -> float:
        """Storage cost of one imem word."""
        return self.word_bits * self.flop_bit

    def mux(self, inputs: int, width: float) -> float:
        """An inputs:1 mux, `width` bits wide."""
        return max(inputs - 1, 0) * width * self.mux2_bit

    def sram_imem(self, words: int, ports: int) -> tuple[float, str]:
        """GE and description of the cheapest macro set for `words` x word_bits."""
        best: tuple[float, str] | None = None
        for m in self.sram:
            if m.ports < ports:
                continue
            n = math.ceil(words / m.words) * math.ceil(self.word_bits / m.bits)
            ge = n * m.um2 / self.nand2_um2
            if best is None or ge < best[0]:
                best = (ge, f"{n}x {m.name}")
        if best is None:
            raise Unbuildable(f"no SRAM macro with {ports} port(s)")
        return best

    def sram_depths(self) -> list[int]:
        return sorted({m.words for m in self.sram})

    # ---- sensitivity -------------------------------------------------------------
    def knobs(self) -> list[str]:
        return ["flop_bit", "mux2_bit", "core", "fifo_control", "unit_port_bits",
                "config_bit", *(f"units.{u}" for u in sorted(self.units))]

    def scaled(self, knob: str, factor: float) -> Cost:
        if knob.startswith("units."):
            u = knob.removeprefix("units.")
            return replace(self, units={**self.units, u: self.units[u] * factor})
        return replace(self, **{knob: getattr(self, knob) * factor})


def cost_from_dict(d: Mapping[str, Any]) -> Cost:
    units = {str(u): float(v) for u, v in d["units"].items()}
    missing = set(unit_types()) - set(units)
    if missing:
        raise ValueError(f"cost model has no price for units {sorted(missing)}")
    macros = tuple(Macro(str(m["name"]), int(m["words"]), int(m["bits"]), int(m["ports"]),
                         float(m["um2"])) for m in d.get("sram", []))
    params = default_isa().params
    return Cost(float(d["flop_bit"]), float(d["mux2_bit"]), float(d["core"]),
                float(d["fifo_control"]), float(d["unit_port_bits"]), float(d["config_bit"]),
                units, int(params["word_bits"]),
                float(d.get("nand2_um2", 1.0)), macros, float(d.get("tile_um2", 0.0)),
                int(params.get("fifo_bits", params["word_bits"])))


def load_cost(path: Path | str | None = None) -> Cost:
    p = Path(path) if path is not None else default_cost()
    return cost_from_dict(yaml.safe_load(p.read_text()))


def _bits(n: int) -> int:
    """Select bits to address one of n choices."""
    return math.ceil(math.log2(n)) if n > 1 else 0


def buildable(arch: Arch, c: Cost) -> str:
    """'' if the architecture can be built, else why not."""
    try:
        area(arch, c)
    except Unbuildable as e:
        return str(e)
    return ""


def area(arch: Arch, c: Cost) -> dict[str, float]:
    if isinstance(arch, PioArch):
        return _pio_area(arch, c)
    return _mesh_area(arch, c)


def _pio_area(a: PioArch, c: Cost) -> dict[str, float]:
    n = a.num_cores
    words = a.imem_words if a.imem_shared else a.imem_words * n
    out = {"core": n * c.core}
    if a.imem_tech == "sram":
        if a.imem_shared:
            out["imem"] = c.sram_imem(a.imem_words, ports=n)[0]
        else:
            out["imem"] = n * c.sram_imem(a.imem_words, ports=1)[0]
    else:
        out["imem"] = words * c.word()
        # one read port per core over the words it can fetch from
        out["imem_read"] = n * c.mux(a.imem_words, c.word_bits)
    out["fifo"] = 2 * n * c.fifo(a.fifo_depth)
    for u in sorted(a.units):
        out[f"unit:{u}"] = n * c.unit(u)
    return out


def _mesh_area(a: MeshArch, c: Cost) -> dict[str, float]:
    k = a.clusters
    cc = a.cores_per_cluster
    banks_c = a.per_cluster(a.banks)
    fifos_c = a.per_cluster(a.fifos)
    out = {"core": a.cores * c.core}
    if a.imem_tech == "sram":
        # each bank is its own macro set; a core selects among its cluster's banks
        out["imem"] = a.banks * c.sram_imem(a.bank_words, ports=1)[0]
        out["imem_read"] = a.cores * c.mux(banks_c, c.word_bits)
    else:
        out["imem"] = a.banks * a.bank_words * c.word()
        out["imem_read"] = a.cores * c.mux(banks_c * a.bank_words, c.word_bits)
    out["fifo"] = a.fifos * c.fifo(a.fifo_depth)
    xbar = 0.0
    config_bits = 0
    for u in unit_types():
        nu = a.per_cluster(a.unit_count(u))
        if nu == 0:
            continue
        out[f"unit:{u}"] = a.unit_count(u) * c.unit(u)
        # each core picks one of nu units; each unit picks one of cc cores
        xbar += k * (cc * c.mux(nu, c.unit_port_bits) + nu * c.mux(cc, c.unit_port_bits))
        config_bits += a.cores * _bits(nu)
    if fifos_c:
        # word-wide paths: each core to any FIFO in its cluster, and back
        xbar += k * (cc * c.mux(fifos_c, c.fifo_bits) + fifos_c * c.mux(cc, c.fifo_bits))
        config_bits += a.cores * 2 * _bits(fifos_c)
    config_bits += a.cores * _bits(banks_c)  # first bank of each core's program
    out["xbar"] = xbar
    out["config"] = config_bits * c.config_bit
    return out


def total(arch: Arch, c: Cost) -> float:
    return sum(area(arch, c).values())

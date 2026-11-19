"""Score (architecture, workload) pairs on area and utilisation; size each family.

Utilisation, two views:
- allocated: the share of each resource class assigned to some program.
- active: the share of cycles the logic does useful work (ESTIMATED, from
  `ResourceDemand.activity`). Storage (imem, FIFOs) counts as active when allocated.
Area fractions: `alloc_area` = area of assigned resources / total; `active_area` =
activity-weighted logic + allocated storage, / total; `overhead` = imem read
ports + crossbar + config / total.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from piolab.isa import default_isa

from .allocate import Allocation, Infeasible, allocate
from .arch import DEFAULT_FIFO_DEPTH, IMEM_TECHS, Arch, MeshArch, PioArch
from .cost import OVERHEAD, Cost, area
from .cost import buildable as unbuildable
from .demand import Workload, unit_types

BANK_WORDS = (2, 4, 8, 16)
#: imem words provisioned per core in the headline comparison (CLAUDE.md §3: 32, as PIO)
HEADROOM = int(default_isa().params["imem_size"])
HEADROOM_SWEEP = (0, 16, 32, 64)
FAMILIES = ("pio_full", "pio_lean", "pio_shared", "mesh_full", "mesh_c2")
FAMILY_TEXT = {
    "pio_full": "PIO-style: private imem, every unit on every core",
    "pio_lean": "PIO-style: private imem, only the unit types some program uses",
    "pio_shared": "PIO-style: one imem shared by all cores (RP2040), lean units; as SRAM it "
                  "needs a port per core, so at most 2 cores",
    "mesh_full": "mesh: pooled cores/banks/units/FIFOs, full crossbar, software allocation",
    "mesh_c2": "mesh: as mesh_full but in clusters of 2 cores (cheaper wiring)",
}


@dataclass(frozen=True)
class Score:
    arch: Arch
    workload: Workload
    reason: str  # "" when feasible
    area: float
    breakdown: dict[str, float]
    alloc: dict[str, float] = field(default_factory=dict)  # cores, imem, fifos, units
    active: dict[str, float] = field(default_factory=dict)  # cores, units
    alloc_area: float = 0.0
    active_area: float = 0.0

    @property
    def feasible(self) -> bool:
        return not self.reason

    @property
    def overhead(self) -> float:
        return sum(self.breakdown.get(k, 0.0) for k in OVERHEAD) / self.area


def _instances(a: Arch) -> tuple[int, int, int, dict[str, int]]:
    """(cores, imem words, FIFOs, units per type) provided by an architecture."""
    if isinstance(a, PioArch):
        words = a.imem_words if a.imem_shared else a.imem_words * a.num_cores
        n = a.num_cores
        return n, words, 2 * n, {u: n for u in sorted(a.units)}
    units = {u: a.unit_count(u) for u in unit_types() if a.unit_count(u)}
    return a.cores, a.banks * a.bank_words, a.fifos, units


def score(arch: Arch, w: Workload, c: Cost, exact: bool = True) -> Score:
    br = area(arch, c)
    tot = sum(br.values())
    res = allocate(arch, w, exact=exact)
    if isinstance(res, Infeasible):
        return Score(arch, w, res.reason, tot, br)
    return _score_alloc(res, c, br, tot)


def _score_alloc(res: Allocation, c: Cost, br: dict[str, float], tot: float) -> Score:
    cores, words, fifos, units = _instances(res.arch)
    # an imem word costs what this architecture pays for one: flops or a share of a macro
    word_ge = br["imem"] / words if words else 0.0
    depth = res.arch.fifo_depth
    tasks = [p.task for p in res.placements]
    unit_alloc = {u: sum(u in t.demand.units for t in tasks) for u in units}
    act = {"core": 0.0, **dict.fromkeys(units, 0.0)}
    alloc_area = active_area = 0.0
    for t in tasks:
        d = t.demand
        a = d.activity(t.load)
        storage = d.imem_words * word_ge + d.fifos * c.fifo(depth)
        alloc_area += c.core + sum(c.unit(u) for u in d.units) + storage
        active_area += a["core"] * c.core + sum(a[u] * c.unit(u) for u in d.units) + storage
        for k, v in a.items():
            act[k] += v
    n_units = sum(units.values())
    return Score(
        res.arch, res.workload, "", tot, br,
        alloc={
            "cores": len(tasks) / cores,
            "imem": sum(t.demand.imem_words for t in tasks) / words if words else 0.0,
            "fifos": sum(t.demand.fifos for t in tasks) / fifos if fifos else 0.0,
            "units": sum(unit_alloc.values()) / n_units if n_units else 0.0,
        },
        active={
            "cores": act["core"] / cores,
            "units": sum(act[u] for u in units) / n_units if n_units else 0.0,
        },
        alloc_area=alloc_area / tot,
        active_area=active_area / tot,
    )


# ---- smallest configuration of each family that fits every workload -----------------


def _mesh_need(ws: Sequence[Workload], bw: int) -> tuple[int, int, int, dict[str, int]]:
    """Totals a full-crossbar mesh needs: (cores, banks, fifos, units per type)."""
    cores = max(len(w.tasks) for w in ws)
    banks = max(sum(t.demand.banks(bw) for t in w.tasks) for w in ws)
    fifos = max(sum(t.demand.fifos for t in w.tasks) for w in ws)
    units = {u: max(sum(u in t.demand.units for t in w.tasks) for w in ws) for u in unit_types()}
    return cores, banks, fifos, {u: n for u, n in units.items() if n}


def _cheapest(cands: list[Arch], ws: Sequence[Workload], c: Cost) -> Arch:
    buildable = [a for a in cands if not unbuildable(a, c)]
    ranked = sorted(buildable, key=lambda a: (sum(area(a, c).values()), repr(a)))
    for a in ranked:
        if all(not isinstance(allocate(a, w), Infeasible) for w in ws):
            return a
    raise ValueError("no candidate fits every workload")


def _bank_sizes(tech: str, c: Cost) -> Sequence[int]:
    return c.sram_depths() if tech == "sram" else BANK_WORDS


def minimal(family: str, ws: Sequence[Workload], c: Cost,
            techs: Sequence[str] = IMEM_TECHS, headroom: int = 0) -> Arch:
    """Smallest configuration of `family` that fits every workload, over imem `techs`.

    `headroom`: provision at least this many imem words per core (room for
    programs not written yet), on top of fitting the workloads.
    """
    n = max(len(w.tasks) for w in ws)
    words = max(headroom, *(t.demand.imem_words for w in ws for t in w.tasks))
    total = max(headroom * n, *(sum(t.demand.imem_words for t in w.tasks) for w in ws))
    union = frozenset(u for w in ws for t in w.tasks for u in t.demand.units)
    depth = max([DEFAULT_FIFO_DEPTH] + [max(t.demand.fifo_tx, t.demand.fifo_rx)
                                         for w in ws for t in w.tasks])
    cands: list[Arch] = []
    for tech in techs:
        if family == "pio_full":
            cands.append(PioArch(family, n, words, frozenset(unit_types()), False, depth,
                                 imem_tech=tech))
        elif family == "pio_lean":
            cands.append(PioArch(family, n, words, union, False, depth, imem_tech=tech))
        elif family == "pio_shared":
            cands.append(PioArch(family, n, total, union, True, depth, imem_tech=tech))
        elif family == "mesh_full":
            for bw in _bank_sizes(tech, c):
                cores, banks, fifos, units = _mesh_need(ws, bw)
                banks = max(banks, math.ceil(headroom * cores / bw))
                cands.append(MeshArch(family, cores, banks, bw, units, fifos, None, depth,
                                      imem_tech=tech))
        elif family == "mesh_c2":
            cands += _cluster_candidates(family, ws, 2, depth, tech, _bank_sizes(tech, c),
                                         headroom)
        else:
            raise ValueError(f"unknown family {family}")
    return _cheapest(cands, ws, c)


def _cluster_candidates(name: str, ws: Sequence[Workload], cc: int, depth: int, tech: str,
                        bank_sizes: Sequence[int], headroom: int = 0) -> list[Arch]:
    """Every per-cluster pool between the obvious lower and upper bounds."""
    out: list[Arch] = []
    k = math.ceil(max(len(w.tasks) for w in ws) / cc)
    for bw in bank_sizes:
        _, banks, fifos, units = _mesh_need(ws, bw)
        biggest = max(t.demand.banks(bw) for w in ws for t in w.tasks)
        floor = max(math.ceil(banks / k), biggest, math.ceil(headroom * cc / bw))
        bank_r = range(floor, max(banks, floor) + 1)
        fifo_r = range(math.ceil(fifos / k), min(2 * cc, fifos) + 1)
        unit_rs = [range(math.ceil(n / k), min(cc, n) + 1) for n in units.values()]
        for b, f, *us in itertools.product(bank_r, fifo_r, *unit_rs):
            pool = {u: n * k for u, n in zip(units, us, strict=True)}
            out.append(MeshArch(name, cc * k, b * k, bw, pool, f * k, cc, depth,
                                imem_tech=tech))
    return out


def minimal_all(ws: Sequence[Workload], c: Cost, headroom: int = 0) -> dict[str, Arch]:
    return {f: minimal(f, ws, c, headroom=headroom) for f in FAMILIES}


def ranking(ws: Sequence[Workload], c: Cost, headroom: int = 0) -> list[tuple[str, float]]:
    """Families by the area of their smallest configuration that fits every workload."""
    sized = minimal_all(ws, c, headroom)
    return sorted(((f, sum(area(a, c).values())) for f, a in sized.items()),
                  key=lambda x: (x[1], x[0]))


@dataclass(frozen=True)
class Sensitivity:
    knob: str
    low: list[str]  # ranking with the knob x0.5
    high: list[str]  # ranking with the knob x2
    flips: bool  # the order changes at either end
    leader_changes: bool  # the smallest family changes at either end


def sensitivity(ws: Sequence[Workload], c: Cost, headroom: int = 0,
                factors: tuple[float, float] = (0.5, 2.0)) -> list[Sensitivity]:
    base = [f for f, _ in ranking(ws, c, headroom)]
    out = []
    for knob in c.knobs():
        lo, hi = ([f for f, _ in ranking(ws, c.scaled(knob, x), headroom)] for x in factors)
        out.append(Sensitivity(knob, lo, hi, lo != base or hi != base,
                               lo[0] != base[0] or hi[0] != base[0]))
    return out


def describe(a: Arch) -> str:
    if isinstance(a, PioArch):
        mem = f"{a.imem_words}w shared" if a.imem_shared else f"{a.imem_words}w/core"
        return (f"{a.num_cores} cores x [{','.join(sorted(a.units)) or 'no units'}], {mem} "
                f"{a.imem_tech}")
    units = ",".join(f"{a.unit_count(u)} {u}" for u in unit_types() if a.unit_count(u))
    wiring = "full xbar" if a.cluster is None else f"{a.clusters} clusters of {a.cluster}"
    return (f"{a.cores} cores, {a.banks}x{a.bank_words}w {a.imem_tech} banks, "
            f"{units + ', ' if units else ''}{a.fifos} FIFOs, {wiring}")


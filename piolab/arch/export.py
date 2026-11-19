"""The architecture study as one JSON-serialisable dict, for <pack docs>/arch-study.html.

Everything is computed by the same functions as arch-study.md; the page only
draws it. Placements are made concrete here (which bank, unit instance and FIFO a
task uses): banks contiguous in placement order, units and FIFOs first-free,
inside each cluster. That is presentation only; the allocator decides feasibility.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .allocate import Allocation, allocate
from .arch import Arch, MeshArch, PioArch
from .cost import OVERHEAD, Cost, area
from .demand import Workload, unit_types
from .evaluate import (
    FAMILIES,
    FAMILY_TEXT,
    HEADROOM,
    HEADROOM_SWEEP,
    describe,
    minimal_all,
    ranking,
    score,
    sensitivity,
)

#: Area classes grouped for the chart (the palette has 8 slots; there are 11 classes).
GROUPS = {"core": ("core",), "imem": ("imem",), "fifo": ("fifo",),
          "units": tuple(f"unit:{u}" for u in unit_types()), "overhead": OVERHEAD}


def _r(x: float, nd: int = 4) -> float:
    """Round for a stable, compact JSON (no float noise between runs)."""
    return round(float(x), nd)


def _structure(a: Arch, c: Cost) -> dict[str, Any]:
    if isinstance(a, PioArch):
        imem_ge = (c.sram_imem(a.imem_words, a.num_cores if a.imem_shared else 1)[0]
                   if a.imem_tech == "sram" else a.imem_words * c.word())
        return {"kind": "pio", "cores": a.num_cores, "imem_words": a.imem_words,
                "imem_shared": a.imem_shared, "units": sorted(a.units),
                "fifo_depth": a.fifo_depth,
                "ge": {"core": _r(c.core, 1), "imem": _r(imem_ge, 1),
                       "fifo": _r(c.fifo(a.fifo_depth), 1),
                       **{u: _r(c.unit(u), 1) for u in sorted(a.units)}}}
    bank_ge = (c.sram_imem(a.bank_words, 1)[0] if a.imem_tech == "sram"
               else a.bank_words * c.word())
    cluster = {"cores": a.cores_per_cluster, "banks": a.per_cluster(a.banks),
               "fifos": a.per_cluster(a.fifos),
               "units": {u: a.per_cluster(a.unit_count(u)) for u in unit_types()
                         if a.unit_count(u)}}
    return {"kind": "mesh", "clusters": [cluster] * a.clusters, "bank_words": a.bank_words,
            "full_xbar": a.cluster is None, "fifo_depth": a.fifo_depth,
            "ge": {"core": _r(c.core, 1), "bank": _r(bank_ge, 1),
                   "fifo": _r(c.fifo(a.fifo_depth), 1),
                   **{u: _r(c.unit(u), 1) for u in unit_types() if a.unit_count(u)}}}


def _arch(a: Arch, c: Cost, sized: bool) -> dict[str, Any]:
    br = area(a, c)
    tot = sum(br.values())
    return {
        "name": a.name, "family": a.family, "sized": sized, "description": describe(a),
        "tech": a.imem_tech, "total": _r(tot, 1), "tiles": _r(c.tiles(tot), 2),
        "area": {k: _r(v, 1) for k, v in sorted(br.items())},
        "groups": {g: _r(sum(br.get(k, 0.0) for k in ks), 1) for g, ks in GROUPS.items()},
        "structure": _structure(a, c),
    }


def _placements(res: Allocation) -> list[dict[str, Any]]:
    a = res.arch
    out: list[dict[str, Any]] = []
    if isinstance(a, PioArch):
        offset = 0
        for i, p in enumerate(res.placements):
            d = p.task.demand
            out.append({"task": i, "core": p.slot, "cluster": 0,
                        "imem": [offset, offset + d.imem_words] if a.imem_shared
                        else [0, d.imem_words],
                        "units": dict.fromkeys(sorted(d.units), 0),  # the core's own unit
                        "fifos": ([0] if d.fifo_tx else []) + ([1] if d.fifo_rx else []),
                        "activity": {k: _r(v) for k, v in sorted(d.activity(p.task.load).items())}})
            offset += d.imem_words if a.imem_shared else 0
        return out
    assert isinstance(a, MeshArch)
    next_bank = [0] * a.clusters
    next_fifo = [0] * a.clusters
    next_unit = [dict.fromkeys(unit_types(), 0) for _ in range(a.clusters)]
    for i, p in enumerate(res.placements):
        d, cl = p.task.demand, p.cluster
        banks = list(range(next_bank[cl], next_bank[cl] + p.banks))
        next_bank[cl] += p.banks
        fifos = list(range(next_fifo[cl], next_fifo[cl] + d.fifos))
        next_fifo[cl] += d.fifos
        units = {}
        for u in sorted(d.units):
            units[u] = next_unit[cl][u]
            next_unit[cl][u] += 1
        out.append({"task": i, "core": p.slot - cl * a.cores_per_cluster, "cluster": cl,
                    "banks": banks, "units": units, "fifos": fifos,
                    "activity": {k: _r(v) for k, v in sorted(d.activity(p.task.load).items())}})
    return out


def _cell(a: Arch, w: Workload, c: Cost) -> dict[str, Any]:
    s = score(a, w, c)
    cell: dict[str, Any] = {"feasible": s.feasible, "reason": s.reason}
    if s.feasible:
        res = allocate(a, w)
        assert isinstance(res, Allocation)
        cell.update({
            "alloc": {k: _r(v) for k, v in sorted(s.alloc.items())},
            "active": {k: _r(v) for k, v in sorted(s.active.items())},
            "alloc_area": _r(s.alloc_area), "active_area": _r(s.active_area),
            "placements": _placements(res),
        })
    return cell


def _workload(w: Workload) -> dict[str, Any]:
    return {"name": w.name, "description": w.description, "tasks": [
        {"program": t.program, "load": t.load, "imem_words": t.demand.imem_words,
         "units": sorted(t.demand.units), "fifos": t.demand.fifos, "pads": t.demand.pads,
         "bit_share": {u: _r(v) for u, v in sorted(t.demand.bit_share.items())},
         "bit_rate": t.demand.bit_rate}
        for t in w.tasks]}


def study_data(ws: Sequence[Workload], archs: Sequence[Arch], c: Cost) -> dict[str, Any]:
    sized = minimal_all(ws, c, HEADROOM)
    rank = [f for f, _ in ranking(ws, c, HEADROOM)]
    all_archs = [(sized[f], True) for f in rank] + [(a, False) for a in archs]
    names = [a.name for a, _ in all_archs]
    if len(set(names)) != len(names):
        raise ValueError(f"architecture names must be unique: {names}")
    prov = []
    for h in HEADROOM_SWEEP:
        m = minimal_all(ws, c, h)
        for f in FAMILIES:
            prov.append({"family": f, "headroom": h, "tech": m[f].imem_tech,
                         "total": _r(sum(area(m[f], c).values()), 1)})
    sens = sensitivity(ws, c, HEADROOM)
    return {
        "meta": {"headroom": HEADROOM, "headroom_sweep": list(HEADROOM_SWEEP),
                 "families": list(FAMILIES), "family_text": FAMILY_TEXT,
                 "unit_types": list(unit_types()), "groups": list(GROUPS),
                 "tile_um2": c.tile_um2, "nand2_um2": c.nand2_um2},
        "archs": [_arch(a, c, s) for a, s in all_archs],
        "workloads": [_workload(w) for w in ws],
        "cells": {a.name: {w.name: _cell(a, w, c) for w in ws} for a, _ in all_archs},
        "provisioning": prov,
        "sensitivity": {"base": rank, "rows": [
            {"knob": s.knob, "low": s.low, "high": s.high, "flips": s.flips,
             "leader_changes": s.leader_changes} for s in sens]},
    }

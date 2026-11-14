"""Static (load-time) allocation of a workload onto an architecture.

PIO: one program per core; every core carries the same units, so binding is trivial
and feasibility is a per-program check. Mesh: the "higher-level software" that
analyses each program's demand and hands out cores, imem banks, units and FIFOs.
With a full crossbar any assignment is as good as any other; with clusters it is
a small bin-packing problem, solved exactly (backtracking) or greedily (first fit)
so the report can show what a cheap allocator loses.

Deterministic: the same inputs give the same allocation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .arch import Arch, MeshArch, PioArch
from .demand import TOTAL_PADS, Task, Workload, unit_types


@dataclass(frozen=True)
class Placement:
    """Where one task runs. `slot` is the core (PIO) or core (mesh) index."""

    task: Task
    slot: int
    cluster: int = 0
    banks: int = 0  # mesh: imem banks taken; PIO: 0


@dataclass(frozen=True)
class Allocation:
    arch: Arch
    workload: Workload
    placements: tuple[Placement, ...]


@dataclass(frozen=True)
class Infeasible:
    arch: Arch
    workload: Workload
    reason: str


AllocResult = Allocation | Infeasible


def allocate(arch: Arch, w: Workload, exact: bool = True) -> AllocResult:
    if w.pads > TOTAL_PADS:
        return Infeasible(arch, w, f"needs {w.pads} pads, the tile has {TOTAL_PADS}")
    if isinstance(arch, PioArch):
        return allocate_pio(arch, w)
    return allocate_mesh(arch, w, exact=exact)


def allocate_pio(a: PioArch, w: Workload) -> AllocResult:
    if len(w.tasks) > a.num_cores:
        return Infeasible(a, w, f"{len(w.tasks)} programs, {a.num_cores} cores")
    for t in w.tasks:
        d = t.demand
        missing = d.units - a.units
        if missing:
            return Infeasible(a, w, f"{d.program} needs {','.join(sorted(missing))}; "
                                    f"cores have {','.join(sorted(a.units)) or 'no units'}")
        if not a.imem_shared and d.imem_words > a.imem_words:
            return Infeasible(a, w, f"{d.program} is {d.imem_words} words; "
                                    f"Core imem is {a.imem_words}")
        if max(d.fifo_tx, d.fifo_rx) > a.fifo_depth:
            return Infeasible(a, w, f"{d.program} needs FIFO depth "
                                    f"{max(d.fifo_tx, d.fifo_rx)}; cores have {a.fifo_depth}")
    if a.imem_shared:
        used = sum(t.demand.imem_words for t in w.tasks)
        if used > a.imem_words:
            return Infeasible(a, w, f"programs total {used} words; shared imem is "
                                    f"{a.imem_words}")
    return Allocation(a, w, tuple(Placement(t, i) for i, t in enumerate(w.tasks)))


# ---- mesh ----------------------------------------------------------------------------

_Cap = tuple[int, ...]  # (cores, banks, fifos, *units in unit_types() order)


def _need(a: MeshArch, t: Task) -> _Cap:
    d = t.demand
    return (1, d.banks(a.bank_words), d.fifos, *(int(u in d.units) for u in unit_types()))


def _fits(cap: _Cap, need: _Cap) -> bool:
    return all(n <= c for c, n in zip(cap, need, strict=True))


def _take(cap: _Cap, need: _Cap) -> _Cap:
    return tuple(c - n for c, n in zip(cap, need, strict=True))


def allocate_mesh(a: MeshArch, w: Workload, exact: bool = True) -> AllocResult:
    for t in w.tasks:
        d = t.demand
        if max(d.fifo_tx, d.fifo_rx) > a.fifo_depth:
            return Infeasible(a, w, f"{d.program} needs FIFO depth "
                                    f"{max(d.fifo_tx, d.fifo_rx)}; pool FIFOs are "
                                    f"{a.fifo_depth}")
    cap0: _Cap = (a.cores_per_cluster, a.per_cluster(a.banks), a.per_cluster(a.fifos),
                  *(a.per_cluster(a.unit_count(u)) for u in unit_types()))
    needs = [_need(a, t) for t in w.tasks]
    # a task that fits no empty cluster can never be placed: name it
    for t, n in zip(w.tasks, needs, strict=True):
        if not _fits(cap0, n):
            return Infeasible(a, w, f"{t.program} needs {_describe(n)}; a cluster has "
                                    f"{_describe(cap0)}")
    assign = (_exact if exact else _greedy)(needs, [cap0] * a.clusters)
    if assign is None:
        total = tuple(sum(col) for col in zip(*needs, strict=True))
        return Infeasible(a, w, f"no assignment fits: workload needs {_describe(total)} in "
                                f"{a.clusters} cluster(s) of {_describe(cap0)}"
                                + ("" if exact else " (greedy)"))
    used = [0] * a.clusters
    out = []
    for t, n, cl in zip(w.tasks, needs, assign, strict=True):
        out.append(Placement(t, cl * a.cores_per_cluster + used[cl], cl, banks=n[1]))
        used[cl] += 1
    return Allocation(a, w, tuple(out))


def _describe(c: _Cap) -> str:
    parts = [f"{c[0]} core(s)", f"{c[1]} bank(s)", f"{c[2]} FIFO(s)"]
    parts += [f"{n} {u}" for u, n in zip(unit_types(), c[3:], strict=True) if n]
    return ", ".join(parts)


def _greedy(needs: Sequence[_Cap], caps: list[_Cap]) -> list[int] | None:
    caps = list(caps)
    out = []
    for n in needs:
        for i, c in enumerate(caps):
            if _fits(c, n):
                caps[i] = _take(c, n)
                out.append(i)
                break
        else:
            return None
    return out


def _exact(needs: Sequence[_Cap], caps: list[_Cap]) -> list[int] | None:
    # biggest tasks first prunes early; identical remaining clusters are tried once
    order = sorted(range(len(needs)), key=lambda i: (tuple(-x for x in needs[i]), i))
    out = [0] * len(needs)

    def place(j: int, caps: list[_Cap]) -> bool:
        if j == len(order):
            return True
        i = order[j]
        tried: set[_Cap] = set()
        for k, c in enumerate(caps):
            if c in tried or not _fits(c, needs[i]):
                continue
            tried.add(c)
            out[i] = k
            if place(j + 1, [*caps[:k], _take(c, needs[i]), *caps[k + 1:]]):
                return True
        return False

    return out if place(0, list(caps)) else None

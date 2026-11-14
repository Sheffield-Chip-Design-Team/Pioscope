"""Architecture descriptions: fixed PIO-style cores, and a software-allocated mesh."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .demand import unit_types

DEFAULT_FIFO_DEPTH = 4
IMEM_TECHS = ("flops", "sram")


def _check_tech(name: str, tech: str) -> None:
    if tech not in IMEM_TECHS:
        raise ValueError(f"{name}: imem '{tech}' is not one of {IMEM_TECHS}")


@dataclass(frozen=True)
class PioArch:
    """N identical cores. Each has its own TX and RX FIFO and one of every unit in `units`.

    imem_shared=False: `imem_words` per core, one read port each.
    imem_shared=True: `imem_words` in total, with one read port per core (RP2040 style).
    """

    name: str
    num_cores: int
    imem_words: int
    units: frozenset[str]
    imem_shared: bool = False
    fifo_depth: int = DEFAULT_FIFO_DEPTH
    family: str = "pio"
    imem_tech: str = "flops"

    def __post_init__(self) -> None:
        _check_tech(self.name, self.imem_tech)
        bad = set(self.units) - set(unit_types())
        if bad:
            raise ValueError(f"{self.name}: unknown unit types {sorted(bad)}")


@dataclass(frozen=True)
class MeshArch:
    """Pools of cores, imem banks, units and FIFOs; software assigns them at load time.

    cluster=None: one full crossbar, every core reaches every pooled resource.
    cluster=k: cores/k clusters, each with an equal share of every pool and a
    crossbar only inside the cluster (cheaper wiring, a harder allocation).
    Counts are totals over all clusters.
    """

    name: str
    cores: int
    banks: int
    bank_words: int
    units: Mapping[str, int]
    fifos: int
    cluster: int | None = None
    fifo_depth: int = DEFAULT_FIFO_DEPTH
    family: str = "mesh"
    imem_tech: str = "flops"

    def __post_init__(self) -> None:
        _check_tech(self.name, self.imem_tech)
        bad = set(self.units) - set(unit_types())
        if bad:
            raise ValueError(f"{self.name}: unknown unit types {sorted(bad)}")
        k = self.clusters
        if self.cluster is not None and self.cores % self.cluster:
            raise ValueError(f"{self.name}: {self.cores} cores not divisible by cluster "
                             f"{self.cluster}")
        for what, n in [("banks", self.banks), ("fifos", self.fifos), *self.units.items()]:
            if n % k:
                raise ValueError(f"{self.name}: {what}={n} not divisible by {k} clusters")

    @property
    def clusters(self) -> int:
        return 1 if self.cluster is None else self.cores // self.cluster

    @property
    def cores_per_cluster(self) -> int:
        return self.cores // self.clusters

    def per_cluster(self, total: int) -> int:
        return total // self.clusters

    def unit_count(self, unit: str) -> int:
        return int(self.units.get(unit, 0))


Arch = PioArch | MeshArch

def default_archs() -> Path:
    from pioscope.packs import active

    return active().arch / "architectures.yaml"


def arch_from_dict(d: Mapping[str, Any]) -> Arch:
    fam = d["family"]
    depth = int(d.get("fifo_depth", DEFAULT_FIFO_DEPTH))
    tech = str(d.get("imem", "flops"))
    if fam == "pio":
        return PioArch(d["name"], int(d["num_cores"]), int(d["imem_words"]),
                       frozenset(d["units"]), bool(d.get("imem_shared", False)), depth,
                       imem_tech=tech)
    if fam == "mesh":
        return MeshArch(d["name"], int(d["cores"]), int(d["banks"]), int(d["bank_words"]),
                        {str(u): int(n) for u, n in d["units"].items()}, int(d["fifos"]),
                        d.get("cluster"), depth, imem_tech=tech)
    raise ValueError(f"{d.get('name')}: unknown family '{fam}' (pio | mesh)")


def load_archs(path: Path | str | None = None) -> list[Arch]:
    p = Path(path) if path is not None else default_archs()
    return [arch_from_dict(d) for d in yaml.safe_load(p.read_text())["architectures"]]

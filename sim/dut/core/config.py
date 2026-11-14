"""Configuration every engine model shares, decoded from a ProgramImage's config image.

Pin roles are the names given by `.pins`, in order. Input-capable roles (ui, uio
pads) and output-capable roles (uo, uio) are each numbered in that order; a model
decides how its instructions address them. A pack's model extends `CoreConfig` with
its own directives' config (ADR-0012).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sim.bench.pads import parse_pad
from sim.image import ProgramImage

from .errors import EngineError

SYSCLK_HZ = 50_000_000


@dataclass(frozen=True)
class Role:
    name: str
    pad: str
    group: str  # ui | uo | uio
    index: int  # pad index within the group
    mode: str  # push_pull | open_drain

    @property
    def can_in(self) -> bool:
        return self.group in ("ui", "uio")

    @property
    def can_out(self) -> bool:
        return self.group in ("uo", "uio")


@dataclass
class CoreConfig:
    roles: list[Role]
    inputs: list[Role]
    outputs: list[Role]
    fifo_tx: int
    fifo_rx: int
    wrap_target: int
    wrap: int
    side_count: int = 0
    side_opt: bool = False
    side_pindirs: bool = False
    side_base: int = 0  # index into outputs
    clkdiv: int = 256  # in 1/256 sysclk cycles
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def idle(self) -> int:
        """Reset level of the output roles."""
        return 0

    @classmethod
    def from_image(cls, image: ProgramImage, fifo_depth: int = 4) -> Any:
        c = image.config
        roles = []
        for name, p in (c.get("pins") or {}).items():
            group, idx = parse_pad(p["pad"])
            roles.append(Role(name, p["pad"], group, idx, p.get("mode", "push_pull")))
        fifo = c.get("fifo") or {"tx": fifo_depth, "rx": fifo_depth}
        wrap = c.get("wrap") or {"target": 0, "wrap": max(image.size - 1, 0)}
        outputs = [r for r in roles if r.can_out]
        cfg = cls(roles, [r for r in roles if r.can_in], outputs,
                  int(fifo["tx"]), int(fifo["rx"]), int(wrap["target"]), int(wrap["wrap"]),
                  raw=dict(c))
        ss = c.get("side_set")
        if ss:
            cfg.side_count, cfg.side_opt, cfg.side_pindirs = (int(ss["count"]), bool(ss["opt"]),
                                                              bool(ss["pindirs"]))
            base = ss.get("base")
            names = [r.name for r in outputs]
            if base is not None and base not in names:
                raise EngineError(f".side_set base={base} is not an output role {names}")
            cfg.side_base = names.index(base) if base is not None else 0
        div = c.get("clkdiv")
        if div:
            cfg.clkdiv = int(div["int"]) * 256 + int(div["frac"])
        return cfg

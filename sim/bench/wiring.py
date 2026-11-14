"""Board wiring: named nets, which DUT pad (if any) each is on, and pulls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sim.image import ProgramImage

from .pads import parse_pad


@dataclass(frozen=True)
class NetSpec:
    pad: str | None = None  # DUT pad, e.g. "uo0"; None = board-only net
    pull: int | None = None  # 1 = pull-up, 0 = pull-down, None = floating

    def __post_init__(self) -> None:
        if self.pad is not None:
            parse_pad(self.pad)


@dataclass
class Wiring:
    nets: dict[str, NetSpec] = field(default_factory=dict)

    def __post_init__(self) -> None:
        pads = [n.pad for n in self.nets.values() if n.pad]
        dup = {p for p in pads if pads.count(p) > 1}
        if dup:
            raise ValueError(f"pads wired to more than one net: {sorted(dup)}")

    @classmethod
    def from_image(cls, image: ProgramImage, pulls: dict[str, int] | None = None) -> Wiring:
        """One net per `.pins` role, named after the role. Open-drain roles get a
        pull-up unless `pulls` says otherwise."""
        pulls = pulls or {}
        nets = {}
        for role, p in image.config.get("pins", {}).items():
            default_pull = 1 if p["mode"] == "open_drain" else None
            nets[role] = NetSpec(pad=p["pad"], pull=pulls.get(role, default_pull))
        return cls(nets)

    def check_against(self, image: ProgramImage) -> list[str]:
        """Inconsistencies between this board and the program's `.pins`."""
        problems = []
        for role, p in image.config.get("pins", {}).items():
            net = self.nets.get(role)
            if net is None:
                problems.append(f"program role '{role}' ({p['pad']}) has no net on the board")
                continue
            if net.pad != p["pad"]:
                problems.append(f"role '{role}': program uses {p['pad']}, board wires {net.pad}")
            if p["mode"] == "open_drain" and net.pull != 1:
                problems.append(f"role '{role}' is open-drain but net has no pull-up")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return {k: {"pad": v.pad, "pull": v.pull} for k, v in self.nets.items()}

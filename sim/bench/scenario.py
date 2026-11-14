"""Scenarios: a backend-neutral description of one bench run, and its result.

A scenario is built by a zero-argument function (so the cocotb process can
rebuild it by import path) and checked after the run against the `Result`
only, which makes checks independent of the backend.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sim.devices.base import Device, Txn
from sim.image import ProgramImage
from sim.wave import Waveform

from .wiring import Wiring


@dataclass
class Attach:
    """A device and the nets its ports connect to ({port: net})."""

    device: Device
    ports: dict[str, str]

    def __post_init__(self) -> None:
        unknown = set(self.ports) - set(self.device.ports)
        if unknown:
            raise ValueError(f"{self.device.name}: no ports {sorted(unknown)}; "
                             f"has {list(self.device.ports)}")


@dataclass
class HostScript:
    """What the host (CPU side) does through the DUT host port."""

    tx: dict[int, list[int]] = field(default_factory=dict)  # tc -> words to push, in order
    poll_rx: Sequence[int] = (0,)  # cores whose RX FIFO is drained every cycle


@dataclass
class Scenario:
    name: str
    wiring: Wiring
    devices: list[Attach]
    max_cycles: int
    image: ProgramImage | None = None
    host: HostScript = field(default_factory=HostScript)
    until: Callable[[RunState], bool] | None = None
    sysclk_hz: float = 50e6
    reset_cycles: int = 10
    requires_engine: bool = True
    seed: int = 0
    on_conflict: str = "raise"  # raise | record
    #: Setup window before each DUT sampling edge (ADR-0006). A timed device edge
    #: inside it reaches the DUT this cycle or the next (seeded). 0 = ideal sampling.
    sample_aperture_s: float = 0.0
    #: With an aperture, also record `dut:<net>`: what the DUT actually sampled.
    record_dut_view: bool = True
    #: Record each device's own clock as `clk:<device>.<name>` (VCD only; exact times).
    record_device_clocks: bool = True

    def __post_init__(self) -> None:
        nets = set(self.wiring.nets)
        names = [a.device.name for a in self.devices]
        if len(set(names)) != len(names):
            raise ValueError(f"device names must be unique: {names}")
        for a in self.devices:
            missing = set(a.ports.values()) - nets
            if missing:
                raise ValueError(f"{a.device.name}: nets {sorted(missing)} not in wiring")
        if self.image is not None:
            problems = self.wiring.check_against(self.image)
            if problems:
                raise ValueError(f"scenario {self.name}: " + "; ".join(problems))


@dataclass
class RunState:
    """Live view for `until` predicates."""

    cycle: int
    devices: dict[str, Device]
    host_rx: dict[int, list[int]]


@dataclass
class Result:
    scenario: str
    backend: str
    cycles: int
    waveform: Waveform
    logs: dict[str, list[Txn]]
    host_rx: dict[int, list[int]]
    host_tx_accepted: dict[int, int]
    conflicts: list[str]
    probes: list[dict[str, int]] = field(default_factory=list)
    #: metastable_windows, late_samples, subcycle_pulses_lost
    stats: dict[str, int] = field(default_factory=dict)

    def txns(self, device: str, kind: str | None = None) -> list[Txn]:
        return [t for t in self.logs[device] if kind is None or t.kind == kind]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario, "backend": self.backend, "cycles": self.cycles,
            "waveform": self.waveform.to_dict(),
            "logs": {k: [t.to_dict() for t in v] for k, v in self.logs.items()},
            "host_rx": {str(k): v for k, v in self.host_rx.items()},
            "host_tx_accepted": {str(k): v for k, v in self.host_tx_accepted.items()},
            "conflicts": self.conflicts,
            "stats": self.stats,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Result:
        return cls(
            scenario=d["scenario"], backend=d["backend"], cycles=int(d["cycles"]),
            waveform=Waveform.from_dict(d["waveform"]),
            logs={k: [Txn.from_dict(t) for t in v] for k, v in d["logs"].items()},
            host_rx={int(k): v for k, v in d["host_rx"].items()},
            host_tx_accepted={int(k): v for k, v in d["host_tx_accepted"].items()},
            conflicts=list(d["conflicts"]),
            stats={k: int(v) for k, v in d.get("stats", {}).items()},
        )

    def save(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path: Path | str) -> Result:
        return cls.from_dict(json.loads(Path(path).read_text()))


Check = Callable[[Result], None]
ScenarioFactory = Callable[[], Scenario]

"""Waveform: per-net change lists sampled once per sysclk.

This module is deliberately dependency-free: decoders import it and nothing
else from the package (piolab/tests/decoders/test_independence.py).

Levels are 0, 1 or X (unknown / contention / undriven without a pull).
"""

from __future__ import annotations

import bisect
import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

X = 2  # unknown level
Z = 3  # drive only: released / high impedance (never a resolved level)
Level = int  # 0 | 1 | X
Drive = int  # 0 | 1 | X | Z


@dataclass
class Waveform:
    sysclk_hz: float
    changes: dict[str, list[tuple[int, Level]]] = field(default_factory=dict)
    length: int = 0  # number of cycles recorded
    #: net -> {cycle: sub-cycle position 0..1} for changes made by timed (async)
    #: device edges. Decoders work on whole cycles; this is for VCD and timing.
    frac: dict[str, dict[int, float]] = field(default_factory=dict)
    #: Display-only exact-time traces (device clocks): name -> [(seconds, level)].
    #: Not part of the cycle grid, so decoders never see them.
    clocks: dict[str, list[tuple[float, int]]] = field(default_factory=dict)
    #: Display-only DUT internal state (engine registers, PC, ...): name -> [(cycle, value)],
    #: recorded on change, with its width in bits. Not nets: decoders never see them.
    probes: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    probe_bits: dict[str, int] = field(default_factory=dict)

    # ---- building -----------------------------------------------------------
    def record(self, cycle: int, levels: Mapping[str, Level],
               frac: Mapping[str, float] | None = None) -> None:
        for net, lvl in levels.items():
            ch = self.changes.setdefault(net, [])
            if not ch or ch[-1][1] != lvl:
                ch.append((cycle, lvl))
                if frac and net in frac:
                    self.frac.setdefault(net, {})[cycle] = frac[net]
        self.length = max(self.length, cycle + 1)

    def record_probes(self, cycle: int, values: Mapping[str, int],
                      bits: Mapping[str, int] | None = None) -> None:
        for name, v in values.items():
            ch = self.probes.setdefault(name, [])
            if not ch or ch[-1][1] != v:
                ch.append((cycle, int(v)))
            if name not in self.probe_bits:
                self.probe_bits[name] = (bits or {}).get(name, 32)

    def time_of(self, net: str, cycle: int) -> float:
        """Exact time (in cycles) of the change recorded at `cycle` on `net`."""
        return cycle + self.frac.get(net, {}).get(cycle, 0.0)

    # ---- queries ------------------------------------------------------------
    @property
    def nets(self) -> list[str]:
        return list(self.changes)

    def at(self, net: str, cycle: int) -> Level:
        ch = self.changes[net]
        i = bisect.bisect_right(ch, (cycle, X + 1)) - 1
        return ch[i][1] if i >= 0 else X

    def edges(self, net: str) -> list[tuple[int, Level]]:
        """(cycle, new_level) for every change after the first sample."""
        return self.changes[net][1:]

    def iter_levels(self, net: str, start: int = 0, end: int | None = None) -> Iterator[Level]:
        end = self.length if end is None else end
        for c in range(start, end):
            yield self.at(net, c)

    def seconds(self, cycles: float) -> float:
        return cycles / self.sysclk_hz

    def has_x(self, net: str, start: int = 0) -> bool:
        return any(lvl == X and c >= start for c, lvl in self.changes[net]) or (
            self.at(net, start) == X
        )

    # ---- persistence --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "sysclk_hz": self.sysclk_hz, "length": self.length,
            "changes": {k: [list(p) for p in v] for k, v in self.changes.items()}}
        if self.frac:  # omitted when empty so synchronous digests are unchanged
            d["frac"] = {n: {str(c): f for c, f in m.items()} for n, m in self.frac.items()}
        if self.clocks:
            d["clocks"] = {n: [list(p) for p in tr] for n, tr in self.clocks.items()}
        if self.probes:  # omitted when empty so stand-in DUT digests are unchanged
            d["probes"] = {n: [list(p) for p in ch] for n, ch in self.probes.items()}
            d["probe_bits"] = dict(self.probe_bits)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Waveform:
        return cls(sysclk_hz=float(d["sysclk_hz"]), length=int(d["length"]),
                   changes={k: [(int(c), int(v)) for c, v in ch] for k, ch in
                            d["changes"].items()},
                   frac={n: {int(c): float(f) for c, f in m.items()}
                         for n, m in d.get("frac", {}).items()},
                   clocks={n: [(float(t), int(v)) for t, v in tr]
                           for n, tr in d.get("clocks", {}).items()},
                   probes={n: [(int(c), int(v)) for c, v in ch]
                           for n, ch in d.get("probes", {}).items()},
                   probe_bits={n: int(b) for n, b in d.get("probe_bits", {}).items()})

    def digest(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()

    def to_vcd(self, path: Path | str, timescale_ns: float | None = None) -> None:
        from vcd import VCDWriter  # local import keeps decoders dependency-free

        period_ps = round(1e12 / self.sysclk_hz) if timescale_ns is None else round(
            timescale_ns * 1000)
        events: list[tuple[int, str, Level]] = []
        for net, ch in self.changes.items():
            events += [(round(self.time_of(net, c) * period_ps), net, lvl) for c, lvl in ch]
        for name, tr in self.clocks.items():
            events += [(round(t * 1e12), name, lvl) for t, lvl in tr]
        for name, ch in self.probes.items():
            events += [(c * period_ps, "probe:" + name, v) for c, v in ch]
        events.sort()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f, VCDWriter(f, timescale="1 ps", date="today") as w:
            vars_ = {n: w.register_var("bench", n.replace(":", "_"), "wire", size=1)
                     for n in self.changes}
            for n in self.clocks:
                label = n.removeprefix("clk:").replace(".", "_")
                vars_[n] = w.register_var("bench.clocks", label, "wire", size=1)
            for n in self.probes:
                # `t0.units.timing.T` -> scope engine.t0.units.timing, variable T
                scope, _, var = ("engine." + n).rpartition(".")
                vars_["probe:" + n] = w.register_var(scope, var, "reg",
                                                     size=self.probe_bits.get(n, 32))
            for t, net, lvl in events:
                # X (= 2) is the unknown level of a 1-bit net; a probe's 2 is just 2
                unknown = lvl == X and not net.startswith("probe:")
                w.change(vars_[net], t, "x" if unknown else lvl)

    def to_gtkw(self, path: Path | str, vcd_path: Path | str) -> None:
        """Write a GTKWave save file for the VCD at `vcd_path`: the bench nets first, then
        one group per core (registers, then a sub-group per unit) and one for the
        shared units, multi-bit signals in hex. `gtkwave X.vcd X.gtkw` opens it laid
        out; groups follow whatever probes the model has, so new cores and units
        appear without changing this code."""
        from vcd.gtkw import GTKWSave

        def name(scope: str, var: str, bits: int) -> str:
            return f"{scope}.{var}" + (f"[{bits - 1}:0]" if bits > 1 else "")

        groups: dict[str, dict[str, list[str]]] = {}  # top -> sub-group ("" = none) -> probes
        for n in self.probes:
            top, _, rest = n.partition(".")
            if not rest:
                top, rest = "engine", n
            sub = rest.rsplit(".", 1)[0] if "." in rest else ""
            groups.setdefault(top, {}).setdefault(sub, []).append(n)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            g = GTKWSave(f)
            g.dumpfile(str(vcd_path))
            with g.group("bench"):
                for n in self.changes:
                    g.trace(f"bench.{n.replace(':', '_')}", datafmt="bin")
            for top, subs in groups.items():
                with g.group(top):
                    for sub, names in subs.items():
                        if sub:
                            g.begin_group(sub, closed=True)
                        for n in names:
                            scope, _, var = ("engine." + n).rpartition(".")
                            bits = self.probe_bits.get(n, 32)
                            g.trace(name(scope, var, bits), alias=n.rpartition(".")[2],
                                    datafmt="hex" if bits > 1 else "bin")
                        if sub:
                            g.end_group(sub, closed=True)

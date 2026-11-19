"""The board: nets, devices and the per-cycle phase order, shared by every backend.

Per sysclk cycle k (ADR-0002), given the DUT outputs O_k registered at the
previous rising edge:

1. pre  = resolve(O_k, device drives D_{k-1})      bus as devices see it
2. each device steps on `pre` and returns its drives D_k
3. L_k  = resolve(O_k, D_k)                         settled bus; conflicts checked here
4. record L_k in the waveform at cycle k
5. present L_k on the DUT inputs; the next rising edge samples them (-> O_{k+1})

In cocotb, steps 1-5 run at the falling edge, so the DUT samples settled values.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping

from piolab.devices.base import Device, Txn
from piolab.wave import Level, Waveform, X

from .nets import BusConflict, Drive, Z, resolve
from .pads import PadIn, PadOut, parse_pad
from .scenario import Result, Scenario


class Board:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.wave = Waveform(scenario.sysclk_hz)
        self.conflicts: list[str] = []
        self.nets = list(scenario.wiring.nets)
        self.pull = {n: s.pull for n, s in scenario.wiring.nets.items()}
        self.pad_of = {n: parse_pad(s.pad) for n, s in scenario.wiring.nets.items() if s.pad}
        self.devices: list[Device] = [a.device for a in scenario.devices]
        # net -> [(device index, port)]
        self.taps: dict[str, list[tuple[int, str]]] = {n: [] for n in self.nets}
        self.portmaps: list[dict[str, str]] = []
        for i, a in enumerate(scenario.devices):
            self.portmaps.append(dict(a.ports))
            for port, net in a.ports.items():
                self.taps[net].append((i, port))
        self.dev_drives: list[dict[str, Drive]] = [
            {p: Z for p in a.device.ports} for a in scenario.devices
        ]
        for d in self.devices:
            d.attach(scenario.sysclk_hz)
        # Sampling window at the DUT input pads (ADR-0006).
        self.aperture_cycles = scenario.sample_aperture_s * scenario.sysclk_hz
        self.rng = random.Random(scenario.seed)
        self.input_nets = [n for n, (g, _) in self.pad_of.items() if g in ("ui", "uio")]
        self.record_dut_view = scenario.record_dut_view and self.aperture_cycles > 0
        self.prev_levels: dict[str, Level] = {}
        self.stats = {"metastable_windows": 0, "late_samples": 0, "subcycle_pulses_lost": 0}

    # ---- per cycle ------------------------------------------------------------
    def dut_drives(self, out: PadOut) -> dict[str, Drive]:
        drives: dict[str, Drive] = {}
        for net, (group, bit) in self.pad_of.items():
            m = 1 << bit
            if group == "uo":
                drives[net] = X if out.uo_x & m else (out.uo >> bit) & 1
            elif group == "uio":
                if out.uio_oe_x & m:
                    drives[net] = X
                elif out.uio_oe & m:
                    drives[net] = X if out.uio_out_x & m else (out.uio_out >> bit) & 1
        return drives

    def _resolve_all(
        self, dut: Mapping[str, Drive], cycle: int, check: bool
    ) -> dict[str, Level]:
        levels: dict[str, Level] = {}
        for net in self.nets:
            drivers: list[tuple[str, Drive]] = []
            if net in dut:
                drivers.append(("dut", dut[net]))
            for i, port in self.taps[net]:
                d = self.dev_drives[i][port]
                if d != Z:
                    drivers.append((f"{self.devices[i].name}.{port}", d))
            lvl, conflict = resolve(drivers, self.pull[net])
            if conflict and check:
                err = BusConflict(net, cycle, tuple(drivers))
                if self.scenario.on_conflict == "raise":
                    raise err
                self.conflicts.append(str(err))
            levels[net] = lvl
        return levels

    def cycle(self, k: int, out: PadOut) -> PadIn:
        dut = self.dut_drives(out)
        pre = self._resolve_all(dut, k, check=False)
        for i, dev in enumerate(self.devices):
            pm = self.portmaps[i]
            view = {port: pre[net] for port, net in pm.items()}
            self.dev_drives[i] = dict(dev.step(k, view))
        levels = self._resolve_all(dut, k, check=True)
        frac = self._change_fracs(k, levels)
        self.wave.record(k, levels, frac)
        sampled = self._sample(levels, frac)
        if self.record_dut_view:
            self.wave.record(k, {f"dut:{n}": sampled[n] for n in self.input_nets})
        self.prev_levels = levels
        return self.pad_in(sampled, out)

    def _change_fracs(self, k: int, levels: Mapping[str, Level]) -> dict[str, float]:
        """Sub-cycle position (0..1) of each net's level change in this cycle, taken
        from the latest timed device drive on that net. Untimed changes are 0."""
        fs = self.scenario.sysclk_hz
        out: dict[str, float] = {}
        for net, lvl in levels.items():
            prev = self.prev_levels.get(net)
            if prev is None or prev == lvl:
                continue
            f = 0.0
            for i, port in self.taps[net]:
                t = self.devices[i].change_times.get(port)
                if t is not None:
                    f = max(f, min(max(t * fs - k, 0.0), 1.0 - 1e-12))
            if f:
                out[net] = f
        return out

    def _sample(self, levels: Mapping[str, Level], frac: Mapping[str, float]
                ) -> dict[str, Level]:
        """What the DUT input flops capture at the edge ending this cycle. A change
        within the aperture before the edge resolves (seeded) to old or new; 'old'
        means the DUT sees it one cycle late."""
        if self.aperture_cycles <= 0:
            return dict(levels)
        sampled = dict(levels)
        for net in self.input_nets:
            f = frac.get(net)
            if f is None or (1.0 - f) >= self.aperture_cycles:
                continue
            self.stats["metastable_windows"] += 1
            if self.rng.random() < 0.5:
                sampled[net] = self.prev_levels[net]
                self.stats["late_samples"] += 1
        return sampled

    def pad_in(self, levels: Mapping[str, Level], out: PadOut) -> PadIn:
        ui = uio = ui_x = uio_x = 0
        # Unconnected uio pads read back what the DUT drives (or 0 when released).
        uio = out.uio_out & out.uio_oe
        for net, (group, bit) in self.pad_of.items():
            m = 1 << bit
            lvl = levels[net]
            if group == "ui":
                if lvl == X:
                    ui_x |= m
                elif lvl:
                    ui |= m
            elif group == "uio":
                uio &= ~m
                if lvl == X:
                    uio_x |= m
                elif lvl:
                    uio |= m
        return PadIn(ui=ui, uio_in=uio, ui_x=ui_x, uio_x=uio_x)

    def _record_clocks(self, cycles: int) -> None:
        """Render every device clock over the run as an exact-time trace."""
        t_end = cycles / self.scenario.sysclk_hz
        for d in self.devices:
            for name, (clk, style) in d.clocks().items():
                n = max(0, math.ceil(-clk.offset_s / clk.period_s))
                trace: list[tuple[float, int]] = [(0.0, 0)]
                level = 0
                while (t := clk.tick(n)) < t_end:
                    if style == "toggle":
                        level ^= 1
                        trace.append((max(t, 0.0), level))
                    else:  # tick: rise at the tick, fall half a period later
                        trace.append((max(t, 0.0), 1))
                        trace.append((min(t + clk.period_s / 2, t_end), 0))
                    n += 1
                self.wave.clocks[f"clk:{d.name}.{name}"] = trace

    def new_events(self) -> list[tuple[str, Txn]]:
        """Device transactions logged since the last call (for live tracing)."""
        out: list[tuple[str, Txn]] = []
        if not hasattr(self, "_seen"):
            self._seen = [0] * len(self.devices)
        for i, d in enumerate(self.devices):
            if len(d.log) > self._seen[i]:
                out += [(d.name, t) for t in d.log[self._seen[i]:]]
                self._seen[i] = len(d.log)
        return out

    def result(self, backend: str, cycles: int, host_rx: dict[int, list[int]],
               host_tx_accepted: dict[int, int], host_log: list[Txn] | None = None
               ) -> Result:
        if self.scenario.record_device_clocks:
            self._record_clocks(cycles)
        return Result(
            scenario=self.scenario.name, backend=backend, cycles=cycles, waveform=self.wave,
            logs={d.name: list(d.log) for d in self.devices}
            | ({"host": host_log} if host_log else {}), host_rx=host_rx,
            host_tx_accepted=host_tx_accepted, conflicts=self.conflicts,
            stats={**self.stats,
                   "subcycle_pulses_lost": sum(d.pulses_lost for d in self.devices)},
        )


class HostDriver:
    """Feeds the host script through the DUT host port, one word per core per cycle."""

    def __init__(self, scenario: Scenario) -> None:
        self.pending = {tc: list(ws) for tc, ws in scenario.host.tx.items()}
        self.accepted = {tc: 0 for tc in scenario.host.tx}
        self.rx: dict[int, list[int]] = {tc: [] for tc in scenario.host.poll_rx}
        self.fs = scenario.sysclk_hz
        self.log: list[Txn] = []  # push / pop events, with cycle and time

    def before_edge(self, dut: object, cycle: int = 0) -> None:
        for tc, words in self.pending.items():
            if words and dut.host_push(tc, words[0]):  # type: ignore[attr-defined]
                self.log.append(Txn(cycle, "push", {"core": tc, "word": words[0]},
                                    cycle / self.fs))
                words.pop(0)
                self.accepted[tc] += 1

    def after_edge(self, dut: object, cycle: int = 0) -> None:
        for tc in self.rx:
            w = dut.host_pop(tc)  # type: ignore[attr-defined]
            if w is not None:
                self.log.append(Txn(cycle, "pop", {"core": tc, "word": w}, cycle / self.fs))
                self.rx[tc].append(w)

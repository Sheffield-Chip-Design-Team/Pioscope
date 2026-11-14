"""Pure-Python backend: the board and a Python DUT in one deterministic cycle loop.

`PySession` is that loop one cycle at a time, so the debugger (sim/debug) steps the
very same run that `run_python` / `pioscope sim` executes.
"""

from __future__ import annotations

from collections.abc import Callable

from sim.bench.board import Board, HostDriver
from sim.bench.loader import PyImageLoader
from sim.bench.scenario import Result, RunState, Scenario
from sim.devices.base import Txn
from sim.dut.base import Dut

#: Source for trace events from a DUT whose `drain_events()` returns bare events.
DUT_SOURCE = "engine"


class PySession:
    """One scenario on a Python DUT, advanced a sysclk cycle at a time.

    A DUT with `drain_events()` contributes its trace events. It returns them as
    (source, event) pairs, the source naming the core (`t0`, `t1`, ...) or `shared`
    (ADR-0010); bare events are logged under `engine`. A DUT with non-empty `probes()`
    has them recorded on change into the waveform (shown in VCD).
    """

    def __init__(self, scenario: Scenario, dut: Dut) -> None:
        self.scenario = scenario
        self.dut = dut
        self.board = Board(scenario)
        self.host = HostDriver(scenario)
        if scenario.image is not None:
            PyImageLoader().load(dut, scenario.image)
        self.out = dut.reset()
        self.devices = {d.name: d for d in self.board.devices}
        self.cycle = 0  # the next cycle to run
        self.finished = False
        self.dut_log: dict[str, list[Txn]] = {}
        self._drain = getattr(dut, "drain_events", None)
        self._probe_bits: dict[str, int] = dict(getattr(dut, "probe_bits", lambda: {})())

    def step(self) -> list[tuple[str, Txn]]:
        """Run one sysclk cycle; return the events it produced, as (source, txn)."""
        if self.finished:
            return []
        k = self.cycle
        n_host = len(self.host.log)
        self.host.before_edge(self.dut, k)
        pins_in = self.board.cycle(k, self.out)
        self.out = self.dut.step(pins_in)
        self.host.after_edge(self.dut, k)
        events = self.board.new_events()
        events += [("host", t) for t in self.host.log[n_host:]]
        if self._drain is not None:
            for e in self._drain():
                src, t = e if isinstance(e, tuple) else (DUT_SOURCE, e)
                self.dut_log.setdefault(src, []).append(t)
                events.append((src, t))
        probes = self.dut.probes()
        if probes:
            self.board.wave.record_probes(k, probes, self._probe_bits)
        self.cycle = k + 1
        sc = self.scenario
        if self.cycle >= sc.max_cycles or (
                sc.until is not None and sc.until(RunState(k, self.devices, self.host.rx))):
            self.finished = True
        return events

    def run(self, on_event: Callable[[str, Txn], None] | None = None) -> Result:
        while not self.finished:
            events = self.step()
            if on_event is not None:
                for src, t in events:
                    on_event(src, t)
        return self.result()

    def result(self) -> Result:
        r = self.board.result("python", self.cycle, self.host.rx, self.host.accepted,
                              self.host.log)
        for src, log in self.dut_log.items():
            r.logs[src] = list(log)
        return r


def run_python(scenario: Scenario, dut: Dut,
               on_event: Callable[[str, Txn], None] | None = None) -> Result:
    """Run `scenario`. `on_event(source, txn)` is called live for every device
    transaction as it happens (e.g. to print a timestamped trace)."""
    return PySession(scenario, dut).run(on_event)

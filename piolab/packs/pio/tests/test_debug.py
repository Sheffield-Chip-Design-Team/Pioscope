"""The debugger on the PIO model: DebugSession, the REPL and the DAP server (VS Code),
and the observability contract (ADR-0010) with a model of two state machines."""

from __future__ import annotations

import functools
import io
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from piolab.bench.backends.python import run_python
from piolab.debug.repl import Repl
from piolab.debug.session import DebugError, DebugSession
from piolab.dut.core import Unit
from piolab.image import ProgramImage
from piolab.packs.pio.models.base import HANDLERS, PioConfig, PioEngine, PioSm

from .scenarios import uart_rx, uart_tx

RX = functools.partial(uart_rx, payload=(0x55, 0xA3))
TX = functools.partial(uart_tx, payload=(0x41,))
RX_SRC = "piolab/packs/pio/programs/uart_rx.pio"
TX_SRC = "piolab/packs/pio/programs/uart_tx.pio"


# ---- DebugSession -----------------------------------------------------------------------


def test_breakpoint_stops_before_the_instruction_issues() -> None:
    s = DebugSession(RX)
    s.add_breakpoint("good_stop")
    st = s.cont()
    assert (st.reason, st.detail["addr"]) == ("breakpoint", 8)
    assert s.eval("isr") >> 24 == 0x55 and not s.core_state()["in_progress"]
    st = s.cont()  # the next byte
    assert st.reason == "breakpoint" and s.eval("isr") >> 24 == 0xA3


def test_locations_resolve() -> None:
    s = DebugSession(RX)
    assert s.resolve("bitloop") == 2
    assert s.resolve("*4") == 4
    assert s.resolve("uart_rx.pio:13") == 2
    assert s.resolve("19") == 8  # a label line: the next instruction
    with pytest.raises(DebugError):
        s.resolve("nowhere")


def test_step_finish_and_stepi() -> None:
    s = DebugSession(RX)
    s.step()  # wait 0 pin 0: until the start bit
    assert s.where()["pc"] == 1 and s.where()["instr"].startswith("set")
    c = s.cycle
    s.stepi(10)
    assert s.cycle == c + 10
    s.finish()
    assert not s.core_state()["in_progress"]


def test_watch_and_catch() -> None:
    s = DebugSession(RX)
    s.add_catch("sm0:push")
    st = s.cont()
    assert st.reason == "catch" and st.detail["event"].data["word"] >> 24 == 0x55
    s.delete()
    s.add_watch("isr_count")
    st = s.cont()
    assert st.reason == "watch"


def test_set_var_and_eval_paths() -> None:
    s = DebugSession(TX)
    s.set_var("x", 0x1234)
    assert s.eval("x") == 0x1234
    assert s.eval("pins.out.tx.level") == 1  # .sm_config high=tx
    assert s.eval("shared.irq.flags") == "0b00000000"
    assert s.eval("config.out_shift") == "right"


def test_runs_to_the_end_and_restarts() -> None:
    s = DebugSession(TX)
    assert s.cont().reason == "finished"
    assert s.result().logs["partner"]
    s.restart()
    assert s.cycle == 0 and not s.finished


def kdb(*cmds: str, factory: Any = RX) -> str:
    buf = io.StringIO()
    r = Repl(DebugSession(factory), stdout=buf)
    for c in cmds:
        r.onecmd(c)
    return buf.getvalue()


def test_repl_session() -> None:
    out = kdb("break good_stop", "continue", "p/x isr", "info registers", "x/3i 0", "list",
              "bt")
    assert f"Breakpoint 1, good_stop () at {RX_SRC}:20" in out
    assert "$ = 0x55000000" in out
    assert "wait 0 pin 0" in out
    assert "#0  pc=8" in out


def test_repl_errors_do_not_escape() -> None:
    out = kdb("break nowhere", "p nothing", "frobnicate", "info widgets")
    assert out.count("error:") == 3 and 'Undefined command: "frobnicate"' in out


def test_debug_batch_cli(capsys) -> None:  # type: ignore[no-untyped-def]
    from piolab.cli import main

    rc = main(["debug", TX_SRC, "--device", "uart:baud=115200",
               "--host-tx", "0x41", "--batch", "-ex", "break 13", "-ex", "c", "-ex", "p/x osr"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"Breakpoint 1, bitloop () at {TX_SRC}:13" in out
    assert "$ = 0x41" in out


# ---- DAP --------------------------------------------------------------------------------


class Dap:
    """A minimal DAP client over the stdio of `piolab dap`."""

    def __init__(self) -> None:
        self.p = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; from piolab.cli import main; sys.exit(main(['dap']))"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.seq = 1
        self.q: queue.Queue[dict[str, Any]] = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self.p.stdout is not None
        while True:
            line = self.p.stdout.readline()
            if not line:
                return
            n = int(line.split(b":")[1])
            self.p.stdout.readline()
            self.q.put(json.loads(self.p.stdout.read(n)))

    def request(self, command: str, **args: Any) -> dict[str, Any]:
        assert self.p.stdin is not None
        seq = self.seq
        self.seq += 1
        body = json.dumps({"seq": seq, "type": "request", "command": command,
                           "arguments": args}).encode()
        self.p.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        self.p.stdin.flush()
        return self.wait(lambda m: m.get("type") == "response" and m["request_seq"] == seq)

    def wait(self, pred: Any, timeout: float = 30.0) -> dict[str, Any]:
        while True:
            m = self.q.get(timeout=timeout)
            if pred(m):
                return m

    def event(self, name: str) -> dict[str, Any]:
        return self.wait(lambda m: m.get("type") == "event" and m["event"] == name)

    def close(self) -> None:
        try:
            self.request("disconnect")
        finally:
            self.p.wait(timeout=10)


def test_dap_session_over_stdio() -> None:
    d = Dap()
    try:
        assert d.request("initialize", adapterID="piolab")["body"]["supportsDataBreakpoints"]
        assert d.request("launch", program=RX_SRC, cycles=20_000,
                         devices=["uart:baud=115200,send=0x55"])["success"]
        d.event("initialized")
        bps = d.request("setBreakpoints", source={"path": RX_SRC},
                        breakpoints=[{"line": 20}])["body"]["breakpoints"]
        assert bps[0]["verified"] and bps[0]["line"] == 20
        d.request("configurationDone")
        assert d.event("stopped")["body"]["reason"] == "entry"
        d.request("continue")
        st = d.event("stopped")["body"]
        assert st["reason"] == "breakpoint" and st["hitBreakpointIds"] == [1]
        frame = d.request("stackTrace", threadId=1)["body"]["stackFrames"][0]
        assert frame["line"] == 20 and frame["source"]["path"].endswith("uart_rx.pio")
        scopes = d.request("scopes", frameId=1)["body"]["scopes"]
        names = [s["name"] for s in scopes]
        assert names[:2] == ["Core sm0", "Registers"] and "Shared" in names
        regs = d.request("variables", variablesReference=scopes[1]["variablesReference"])
        isr = next(v for v in regs["body"]["variables"] if v["name"] == "isr")
        assert isr["value"] == "0x55000000" and isr["evaluateName"] == "sm0.regs.isr"
        ev = d.request("evaluate", expression="p/x isr", context="repl")["body"]
        assert ev["result"] == "$ = 0x55000000"
        d.request("next")
        assert d.event("stopped")["body"]["reason"] == "step"
        d.request("setBreakpoints", source={"path": RX_SRC}, breakpoints=[])
        d.request("continue")
        d.event("terminated")
    finally:
        d.close()


def test_dap_disassembly_shows_the_rp2040_words() -> None:
    d = Dap()
    try:
        d.request("initialize", adapterID="piolab")
        d.request("launch", program=RX_SRC, cycles=20_000, devices=["uart:baud=115200,send=0x55"])
        d.event("initialized")
        d.request("configurationDone")
        d.event("stopped")
        rows = d.request("disassemble", memoryReference="0x0000",
                         instructionCount=3)["body"]["instructions"]
        assert rows[0]["symbol"] == "start" and rows[0]["instruction"].startswith("wait")
        assert rows[0]["instructionBytes"] == "2020"
    finally:
        d.close()


# ---- observability: a model with two SMs, an extra unit and a shared unit ----------------


class EdgeSeen(Unit):
    """Counts changes of the SM's in-base pin (an observe-only unit)."""

    name = "edge_seen"

    def __init__(self) -> None:
        self.last: int | None = None
        self.edges = 0

    def on_cycle(self, owner: Any) -> None:
        lvl = owner.gpio_in(int(owner.sm["in"]))
        if self.last is not None and lvl != self.last:
            self.edges += 1
        self.last = lvl

    def state(self) -> dict[str, Any]:
        return {"edges": self.edges}

    def probes(self) -> dict[str, int]:
        return {"edges": self.edges}


class TwoSms(PioEngine):
    """sm0 and sm1 run the same program in lockstep; sm0 has an extra unit."""

    def build(self, image: ProgramImage) -> None:
        super().build(image)
        cfg = PioConfig.from_image(image)
        self.cores[0].units.append(EdgeSeen())
        sm1 = PioSm(image, cfg, HANDLERS, self.fs, "sm1", self, 1)
        self.cores.append(sm1)

    def host_push(self, tc: int, word: int) -> bool:
        return all(t.txf.push(word) for t in self.cores)


def test_trace_and_probes_follow_the_model(tmp_path: Path) -> None:
    r = run_python(TX(), TwoSms())
    assert {"sm0", "sm1"} <= set(r.logs)
    w = r.waveform
    assert {"sm0.pc", "sm1.pc", "sm0.units.edge_seen.edges", "shared.gpio.out",
            "shared.irq.flags"} <= set(w.probes)
    w.to_vcd(tmp_path / "x.vcd")
    text = (tmp_path / "x.vcd").read_text()
    for scope in ("sm0", "sm1", "shared", "gpio", "edge_seen"):
        assert f"$scope module {scope} $end" in text


def test_session_breaks_on_any_sm() -> None:
    s = DebugSession(TX, dut=TwoSms)
    assert s.names == ["sm0", "sm1"]
    bp = s.add_breakpoint("sm1:13")
    assert set(bp.addrs) == {1}
    st = s.cont()
    assert st.reason == "breakpoint" and st.detail["core"] == 1 and s.selected == 1
    assert s.eval("sm0.pc") == 2


def test_vs_code_sees_every_sm() -> None:
    d = Dap()
    try:
        d.request("initialize", adapterID="piolab")
        d.request("launch", program=TX_SRC, devices=["uart:baud=115200"],
                  hostTx=["0x41"], cycles=20_000,
                  dut="piolab.packs.pio.tests.test_debug:TwoSms")
        d.event("initialized")
        d.request("configurationDone")
        d.event("stopped")
        threads = d.request("threads")["body"]["threads"]
        assert [t["name"] for t in threads] == ["sm0", "sm1"]
    finally:
        d.close()

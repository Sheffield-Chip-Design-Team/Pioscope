"""cocotb backend: the same Board and devices, driven against an HDL DUT.

Two halves:

* `drive_scenario(tb, scenario)` is the cocotb coroutine that runs inside the
  simulator. It applies ADR-0002's phase order at each falling edge of `clk`
  using the shared `Board`, so results are comparable cycle-for-cycle with the
  pure-Python backend.
* `run_rtl(factory, ...)` runs in the pytest process. It builds `tb.v` + RTL
  with cocotb_tools.runner, launches the simulator with the cocotb entry test
  (`pioscope.bench.backends.cocotb_entry`) and loads the saved Result.

The engine host port (FIFOs/IRQ) has no RTL mapping yet: `RtlHost` is the
seam where a backdoor adapter will go once the RTL exposes FIFOs.
"""

from __future__ import annotations

import base64
import functools
import importlib
import os
import pickle
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from pioscope.bench.board import Board, HostDriver
from pioscope.bench.loader import MemFileLoader
from pioscope.bench.pads import PadIn, PadOut
from pioscope.bench.report import format_event
from pioscope.bench.scenario import Result, RunState, Scenario, ScenarioFactory

RTL_DIR = Path(__file__).resolve().parents[1] / "rtl"
#: The Tiny Tapeout harness: instantiates a `tt_um_*` top with the ui/uo/uio pads.
TB = RTL_DIR / "tb.v"
#: The stand-in RTL when no sources are given: the Tiny Tapeout example design, whose
#: Python twin is `pioscope.dut.TtTemplateDut` (the bench parity test uses both).
DEFAULT_SOURCES = [RTL_DIR / "tt_template.v"]


# ------------------------------------------------------------------------------
# Inside the simulator
# ------------------------------------------------------------------------------


class RtlHost:
    """Host-port adapter for RTL. Default: no host port (pushes are refused)."""

    def push(self, tb: Any, tc: int, word: int) -> bool:
        return False

    def pop(self, tb: Any, tc: int) -> int | None:
        return None


def _read(sig: Any) -> tuple[int, int]:
    """(value, x_mask) from a cocotb LogicArray handle value."""
    s = str(sig.value)
    v = x = 0
    for ch in s:
        v <<= 1
        x <<= 1
        if ch == "1":
            v |= 1
        elif ch != "0":
            x |= 1
    return v, x


def _write(sig: Any, value: int, xmask: int, width: int = 8) -> None:
    if not xmask:
        sig.value = value
        return
    from cocotb.types import LogicArray

    bits = "".join(
        "X" if (xmask >> i) & 1 else str((value >> i) & 1) for i in reversed(range(width))
    )
    sig.value = LogicArray(bits)


def read_outputs(tb: Any) -> PadOut:
    uo, uo_x = _read(tb.uo_out)
    uio, uio_x = _read(tb.uio_out)
    oe, oe_x = _read(tb.uio_oe)
    return PadOut(uo=uo, uio_out=uio, uio_oe=oe, uo_x=uo_x, uio_out_x=uio_x, uio_oe_x=oe_x)


class _HostPort:
    """Adapts RtlHost(tb, ...) to the Dut host-port methods HostDriver expects."""

    def __init__(self, host: RtlHost, tb: Any) -> None:
        self.host, self.tb = host, tb

    def host_push(self, tc: int, word: int) -> bool:
        return self.host.push(self.tb, tc, word)

    def host_pop(self, tc: int) -> int | None:
        return self.host.pop(self.tb, tc)


async def drive_scenario(tb: Any, scenario: Scenario, host: RtlHost | None = None,
                         trace: bool = False) -> Result:
    """With `trace`, every device/host event is also written to the cocotb log as it
    happens (cocotb prefixes the simulator time)."""
    import cocotb
    from cocotb.clock import Clock
    from cocotb.triggers import ClockCycles, FallingEdge

    port = _HostPort(host or RtlHost(), tb)
    period_ns = 1e9 / scenario.sysclk_hz
    cocotb.start_soon(Clock(tb.clk, period_ns, unit="ns").start())
    tb.ena.value = 1
    tb.rst_n.value = 0
    tb.ui_in.value = 0
    tb.uio_in.value = 0
    await ClockCycles(tb.clk, scenario.reset_cycles)
    await FallingEdge(tb.clk)
    tb.rst_n.value = 1

    board = Board(scenario)
    hostd = HostDriver(scenario)
    devices = {d.name: d for d in board.devices}
    cycles = scenario.max_cycles
    for k in range(scenario.max_cycles):
        out = read_outputs(tb)
        n_host = len(hostd.log)
        hostd.before_edge(port, k)
        pins: PadIn = board.cycle(k, out)
        _write(tb.ui_in, pins.ui, pins.ui_x)
        _write(tb.uio_in, pins.uio_in, pins.uio_x)
        await FallingEdge(tb.clk)
        hostd.after_edge(port, k)
        if trace:
            for src, t in [*board.new_events(), *(("host", t) for t in hostd.log[n_host:])]:
                tb._log.info(format_event(src, t))
        if scenario.until is not None and scenario.until(RunState(k, devices, hostd.rx)):
            cycles = k + 1
            break
    return board.result("rtl", cycles, hostd.rx, hostd.accepted, hostd.log)


# ------------------------------------------------------------------------------
# In the pytest / CLI process
# ------------------------------------------------------------------------------


def factory_ref(factory: ScenarioFactory) -> tuple[str, dict[str, Any]]:
    """An import path the simulator process can rebuild the scenario from."""
    kwargs: dict[str, Any] = {}
    fn: Callable[..., Scenario] = factory
    if isinstance(factory, functools.partial):
        if factory.args:
            raise ValueError("use keyword arguments in partial() scenario factories")
        kwargs = dict(factory.keywords)
        fn = factory.func
    ref = f"{fn.__module__}:{fn.__qualname__}"
    if "<locals>" in ref:
        raise ValueError(f"scenario factory {ref} must be a module-level function")
    return ref, kwargs


def encode_kwargs(kwargs: dict[str, Any]) -> str:
    """Factory kwargs for the simulator process (same machine, same code: pickle is fine)."""
    return base64.b64encode(pickle.dumps(kwargs)).decode()


def decode_kwargs(blob: str) -> dict[str, Any]:
    out: dict[str, Any] = pickle.loads(base64.b64decode(blob)) if blob else {}
    return out


def load_factory(ref: str, kwargs: dict[str, Any]) -> Scenario:
    mod, _, attr = ref.partition(":")
    obj: Any = importlib.import_module(mod)
    for part in attr.split("."):
        obj = getattr(obj, part)
    sc: Scenario = obj(**kwargs)
    return sc


def run_rtl(
    factory: ScenarioFactory,
    sim: str = "icarus",
    sources: Sequence[Path | str] | None = None,
    build_dir: Path | str | None = None,
    defines: dict[str, object] | None = None,
    waves: bool = False,
    trace: bool = False,
) -> Result:
    from cocotb_tools.runner import get_runner

    ref, kwargs = factory_ref(factory)
    scenario = factory()  # also validates in this process
    srcs = [Path(s) for s in (sources or DEFAULT_SOURCES)]
    bdir = Path(build_dir or Path.cwd() / "sim_build" / f"pioscope_{sim}")
    tdir = bdir / "runs" / scenario.name
    tdir.mkdir(parents=True, exist_ok=True)
    result_path = tdir / "result.json"
    if result_path.exists():
        result_path.unlink()
    plusargs: list[str] = []
    if scenario.image is not None:
        loader = MemFileLoader(tdir / "mem")
        if scenario.image.encodable:
            loader.load(None, scenario.image)
            plusargs = loader.plusargs

    runner = get_runner(sim)
    build_args: list[str] = []
    if sim == "verilator":
        build_args = ["--timing", "-Wno-fatal"]
    runner.build(
        sources=[*srcs, TB],
        includes=sorted({s.resolve().parent for s in srcs}),
        defines=defines or {},
        hdl_toplevel="tb",
        build_dir=bdir,
        build_args=build_args,
        timescale=("1ns", "1ps"),
        waves=waves,
    )
    env = {
        "PIOSCOPE_SCENARIO": ref,
        "PIOSCOPE_SCENARIO_ARGS": encode_kwargs(kwargs),
        "PIOSCOPE_RESULT": str(result_path),
        "PIOSCOPE_TRACE": "1" if trace else "",
        "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")]),
    }
    runner.test(
        test_module="pioscope.bench.backends.cocotb_entry",
        hdl_toplevel="tb",
        build_dir=bdir,
        test_dir=tdir,
        extra_env=env,
        plusargs=plusargs,
        waves=waves,
    )
    if not result_path.exists():
        raise RuntimeError(f"RTL run of {scenario.name} produced no result; see {tdir}")
    return Result.load(result_path)

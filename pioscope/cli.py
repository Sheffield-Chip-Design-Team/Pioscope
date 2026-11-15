"""The `pioscope` command: `pioscope [--isa PACK] asm | sim | rtl | debug | dap | metrics |
isa-docs | arch | vscode | new-pack`.

Source files are assembled with the ISA pack of their suffix (`.pio`, ...); `--isa`
(or $PIOSCOPE_ISA) picks the active pack for everything else: generated documents,
the arch study, metrics and the default programs (ADR-0012)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pioscope.asm import AsmErrors, assemble_file, render_listing
from pioscope.bench import Attach, HostScript, Result, Scenario, Wiring
from pioscope.bench.backends.cocotb_rtl import load_factory
from pioscope.devices import I2cSlave, SpiSlave, Txn, UartConfig, UartPartner
from pioscope.image import ProgramImage

# ---- pioscope asm ----------------------------------------------------------------------------

def asm_main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pioscope asm", description="Assemble a program (the ISA "
                                 "pack is chosen by the file's suffix).")
    ap.add_argument("source", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="image JSON (default: <source>.json)")
    ap.add_argument("--listing", type=Path, help="write a listing file")
    ap.add_argument("--hex", type=Path, metavar="DIR", help="write prog.hex/cfg.json for RTL")
    args = ap.parse_args(argv)
    try:
        image = assemble_file(args.source)
    except AsmErrors as e:
        print(e.render(), file=sys.stderr)
        return 1
    except OSError as e:
        print(f"pioscope asm: {e}", file=sys.stderr)
        return 1
    for w in image.warnings:
        print(f"warning: {w}", file=sys.stderr)
    out = args.output or args.source.with_suffix(".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save_json(out)
    if args.listing:
        args.listing.parent.mkdir(parents=True, exist_ok=True)
        args.listing.write_text(render_listing(image))
    if args.hex:
        try:
            image.write_mem(args.hex)
        except ValueError as e:
            print(f"pioscope asm: {e}", file=sys.stderr)
            return 1
    print(f"{image.name}: {image.size}/{image.imem_size} slots -> {out}")
    return 0


# ---- device presets for pioscope sim ----------------------------------------------------------

Preset = Callable[[ProgramImage, argparse.Namespace], Attach]


def _roles(image: ProgramImage) -> set[str]:
    return set(image.config.get("pins", {}))


def _uart(image: ProgramImage, a: argparse.Namespace) -> Attach:
    tim = (image.config.get("tim") or {}).get("default", {})
    baud = a.baud or tim.get("rate")
    if not baud:
        raise SystemExit(
            "uart_partner: the program does not give a baud rate; pass --baud "
            '(VS Code: add "baud": N to the launch configuration)'
        )
    ports = {}
    if "tx" in _roles(image):
        ports["rx"] = "tx"
    if "rx" in _roles(image):
        ports["tx"] = "rx"
    sends = [int(x, 0) for x in a.uart_send.split(",")] if a.uart_send else []
    return Attach(UartPartner("uart_partner", UartConfig(baud, ppm=a.ppm), send=sends), ports)


def _spi(image: ProgramImage, a: argparse.Namespace) -> Attach:
    roles = _roles(image)
    dev = SpiSlave("spi_slave", mode=a.mode, use_cs="cs" in roles)
    return Attach(dev, {p: p for p in dev.ports if p in roles})


def _i2c(image: ProgramImage, a: argparse.Namespace) -> Attach:
    return Attach(I2cSlave("i2c_slave", a.i2c_addr, stretch_cycles=a.stretch),
                  {"scl": "scl", "sda": "sda"})


PRESETS: dict[str, Preset] = {"uart_partner": _uart, "spi_slave": _spi, "i2c_slave": _i2c}


def _add_log_args(ap: argparse.ArgumentParser) -> None:
    g = ap.add_argument_group("logging")
    g.add_argument("--log-limit", type=int, default=40,
                   help="events printed in the timeline (0 = all; default 40)")
    g.add_argument("--log", type=Path, metavar="FILE", help="write the full timestamped log")
    g.add_argument("--source", action="append", metavar="NAME",
                   help="only show events from this device (or 'host'); repeatable")
    g.add_argument("--trace", action="store_true",
                   help="print every event live, with its timestamp, as the run progresses")


def _print_result(r: Result, a: argparse.Namespace) -> None:
    from pioscope.bench.report import render_log

    print(f"{r.scenario} on {r.backend}: {r.cycles} cycles "
          f"({r.cycles / r.waveform.sysclk_hz * 1e6:.3f} us)")
    if any(r.stats.values()):
        print("  stats: " + ", ".join(f"{k}={v}" for k, v in r.stats.items()))
    counts = ", ".join(f"{d}={len(t)}" for d, t in r.logs.items())
    print(f"  events: {counts}")
    if not a.trace:
        print(render_log(r, limit=a.log_limit, sources=a.source))
    for tc, words in r.host_rx.items():
        if words:
            print(f"  host rx t{tc}: {[hex(w) for w in words]}")
    for c in r.conflicts[:5]:
        print(f"  conflict: {c}")
    if a.log:
        a.log.parent.mkdir(parents=True, exist_ok=True)
        a.log.write_text(render_log(r, limit=0, sources=a.source) + "\n")
        print(f"  log: {a.log}")


def _quiet_pipe() -> None:
    """Let `pioscope sim ... | head` end quietly instead of raising BrokenPipeError."""
    import signal

    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def _add_scenario_args(ap: argparse.ArgumentParser, image_help: str) -> None:
    ap.add_argument("image", type=Path, nargs="?", help=image_help)
    ap.add_argument("--scenario", help="module:factory (overrides devices/wiring)")
    ap.add_argument("--device", action="append", default=[], choices=sorted(PRESETS))
    ap.add_argument("--cycles", type=int, default=100_000)
    ap.add_argument("--host-tx", help="comma-separated words pushed to SM0's TX FIFO")
    ap.add_argument("--baud", type=float)
    ap.add_argument("--ppm", type=float, default=0.0)
    ap.add_argument("--uart-send", help="comma-separated bytes the UART partner transmits")
    ap.add_argument("--mode", type=int, default=0, help="SPI mode")
    ap.add_argument("--i2c-addr", type=lambda s: int(s, 0), default=0x42)
    ap.add_argument("--stretch", type=int, default=0)


def _load_image(path: Path) -> ProgramImage:
    """An image JSON from `pioscope asm`, or source (assembled on the fly with the ISA pack
    of its suffix)."""
    if path.suffix == ".json":
        return ProgramImage.load_json(path)
    return assemble_file(path)


def _scenario_factory(ap: argparse.ArgumentParser, a: argparse.Namespace
                      ) -> Callable[[], Scenario]:
    """A zero-argument factory, so a run can be rebuilt from scratch (debugger restart)."""
    if a.scenario:
        return lambda: load_factory(a.scenario, {})
    if a.image is None:
        ap.error("give a program (source or image JSON) or --scenario")
    image = _load_image(a.image)

    def make() -> Scenario:
        devs = [PRESETS[d](image, a) for d in a.device]
        host = HostScript(tx={0: [int(x, 0) for x in a.host_tx.split(",")]} if a.host_tx
                          else {})
        return Scenario(image.name, Wiring.from_image(image), devs, a.cycles, image=image,
                        host=host)

    return make


def sim_main(argv: Sequence[str] | None = None) -> int:
    _quiet_pipe()
    ap = argparse.ArgumentParser(prog="pioscope sim",
                                 description="Run a program or scenario on the pure-Python bench.")
    _add_scenario_args(ap, "program: source (any ISA pack's suffix) or image JSON from "
                       "pioscope asm")
    ap.add_argument("--dut", default="stub", help="stub | tt_template | engine | module:callable")
    ap.add_argument("--vcd", type=Path)
    _add_log_args(ap)
    a = ap.parse_args(argv)

    from pioscope.bench.backends.python import run_python
    from pioscope.dut import dut_factory

    sc = _scenario_factory(ap, a)()
    dut = dut_factory(a.dut, sc.image)()
    if sc.requires_engine and not dut.is_engine:
        print(f"note: DUT '{a.dut}' is a stand-in; the engine program is loaded but not run",
              file=sys.stderr)
    on_event = None
    if a.trace:
        from pioscope.bench.report import HEADER, format_event

        print(HEADER)
        want = set(a.source) if a.source else None

        def on_event(src: str, t: Txn) -> None:
            if want is None or src in want:
                print(format_event(src, t), flush=True)

    r = run_python(sc, dut, on_event=on_event)
    _print_result(r, a)
    if a.vcd:
        r.waveform.to_vcd(a.vcd)
        print(f"  vcd: {a.vcd}")
    return 0


def rtl_main(argv: Sequence[str] | None = None) -> int:
    _quiet_pipe()
    ap = argparse.ArgumentParser(prog="pioscope rtl",
                                 description="Run a scenario against RTL via cocotb.")
    ap.add_argument("--scenario", required=True, help="module:factory")
    ap.add_argument("--sim", default="icarus", choices=["icarus", "verilator"])
    ap.add_argument("--src", action="append", type=Path,
                    help="RTL sources (default: the packaged TT template stand-in)")
    ap.add_argument("--vcd", type=Path, help="also write the bench-level waveform as VCD")
    ap.add_argument("--waves", action="store_true", help="simulator waveform dump")
    _add_log_args(ap)
    a = ap.parse_args(argv)
    import importlib

    from pioscope.bench.backends.cocotb_rtl import run_rtl

    mod, _, attr = a.scenario.partition(":")
    factory = getattr(importlib.import_module(mod), attr)
    # --trace: events are logged live inside the simulator (cocotb log, sim time prefix)
    r = run_rtl(factory, sim=a.sim, sources=a.src, waves=a.waves, trace=a.trace)
    _print_result(r, a)
    if a.vcd:
        r.waveform.to_vcd(a.vcd)
    return 0


# ---- pioscope (dispatcher) ------------------------------------------------------------------


def _metrics(argv: Sequence[str]) -> int:
    from pioscope.scripts.metrics import main as metrics_main

    return metrics_main(list(argv))


def _isa_docs(argv: Sequence[str]) -> int:
    from pioscope.scripts.gen_isa_docs import main as docs_main

    return docs_main(list(argv))


def _isa_check(argv: Sequence[str]) -> int:
    from pioscope.isa.check import main as check_main

    return check_main(list(argv))


def _debug(argv: Sequence[str]) -> int:
    from pioscope.debug.repl import debug_main

    return debug_main(list(argv), _scenario_factory,
                      lambda ap: _add_scenario_args(ap, "program: source or image JSON"))


def _dap(argv: Sequence[str]) -> int:
    from pioscope.debug.dap import dap_main

    return dap_main(list(argv))


def _vscode(argv: Sequence[str]) -> int:
    from pioscope.vscode.build import main as vscode_main

    return vscode_main(list(argv))


def _new_pack(argv: Sequence[str]) -> int:
    from pioscope.scripts.new_pack import main as new_pack_main

    return new_pack_main(list(argv))


def _arch(argv: Sequence[str]) -> int:
    from pioscope.arch.report import main as arch_main

    return arch_main(list(argv))


COMMANDS: dict[str, tuple[Callable[[Sequence[str]], int], str]] = {
    "asm": (asm_main, "assemble a program into an image JSON / listing / hex"),
    "sim": (sim_main, "run a program or scenario on the pure-Python bench"),
    "rtl": (rtl_main, "run a scenario against RTL through cocotb"),
    "metrics": (_metrics, "per-program static ISA metrics"),
    "isa-docs": (_isa_docs, "regenerate the active pack's isa.md + isa-encoding.adoc/.html"),
    "isa-check": (_isa_check, "check the pack's yaml against its model, directives, programs "
                              "and docs"),
    "debug": (_debug, "step through a program on its ISA pack's engine model, GDB style"),
    "dap": (_dap, "Debug Adapter Protocol server for VS Code (the `vscode` extension)"),
    "vscode": (_vscode, "build the VS Code extension (.vsix) for the installed ISA packs"),
    "new-pack": (_new_pack, "scaffold a new ISA pack project (own repo, entry-point registered)"),
    "arch": (_arch, "architecture study: PIO-style vs mesh, area and utilisation"),
}


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and (args[0] == "--isa" or args[0].startswith("--isa=")):
        from pioscope.packs import packs, select

        name = args[0].partition("=")[2] or (args[1] if len(args) > 1 else "")
        args = args[1:] if "=" in args[0] else args[2:]
        if name not in packs():
            print(f"pioscope: unknown ISA pack '{name}' (available: {', '.join(packs())})",
                  file=sys.stderr)
            return 2
        select(name)
    usage = "pioscope [--isa PACK] <command> [options]\n\ncommands:\n" + "\n".join(
        f"  {name:9s} {helptext}" for name, (_, helptext) in COMMANDS.items())
    usage += "\n\n`pioscope <command> --help` shows the options of each command."
    from pioscope.packs import active, packs

    usage += (f"\nISA packs: {', '.join(packs())} (active: {active().name}; "
              "--isa or $PIOSCOPE_ISA to choose)")
    if not args or args[0] in ("-h", "--help"):
        print(f"usage: {usage}")
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    if cmd not in COMMANDS:
        print(f"pioscope: unknown command '{cmd}'\nusage: {usage}", file=sys.stderr)
        return 2
    return COMMANDS[cmd][0](rest)

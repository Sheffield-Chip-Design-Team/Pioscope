"""The `piolab` command: `piolab <command> [--isa PACK] [options]`, with the commands
asm | sim | rtl | debug | dap | models | metrics | isa-docs | isa-check | arch | vscode |
new-pack.

Source files are assembled with the ISA pack of their suffix (`.pio`, ...); a bare program
name (`uart_rx`) is looked up in the active pack. `--isa` (anywhere on the line), else
$PIOLAB_ISA, else `[tool.piolab] pack` in the project's pyproject.toml, picks the active
pack: generated documents, the arch study, metrics, program names (ADR-0012, ADR-0015)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from piolab.asm import AsmErrors, assemble_file, render_listing
from piolab.bench import Result
from piolab.devices import Txn
from piolab.run import RunError, RunOptions, add_run_args, default_output, resolve_program

# ---- piolab asm ----------------------------------------------------------------------------

def asm_main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="piolab asm", description="Assemble a program (the ISA "
                                 "pack is chosen by the file's suffix).")
    ap.add_argument("source", help="a path, or a program name of the active pack (uart_rx)")
    ap.add_argument("-o", "--output", type=Path,
                    help="image JSON (default: <out>/<name>.json, [tool.piolab] out)")
    ap.add_argument("--listing", nargs="?", const="", metavar="FILE",
                    help="write a listing (no FILE: <out>/<name>.lst)")
    ap.add_argument("--hex", type=Path, metavar="DIR", help="write prog.hex/cfg.json for RTL")
    args = ap.parse_args(argv)
    try:
        src = resolve_program(args.source)
        image = assemble_file(src)
    except AsmErrors as e:
        print(e.render(), file=sys.stderr)
        return 1
    except (OSError, RunError) as e:
        print(f"piolab asm: {e}", file=sys.stderr)
        return 1
    for w in image.warnings:
        print(f"warning: {w}", file=sys.stderr)
    out = args.output or default_output(src.stem, ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save_json(out)
    if args.listing is not None:
        lst = Path(args.listing) if args.listing else default_output(src.stem, ".lst")
        lst.parent.mkdir(parents=True, exist_ok=True)
        lst.write_text(render_listing(image))
    if args.hex:
        try:
            image.write_mem(args.hex)
        except ValueError as e:
            print(f"piolab asm: {e}", file=sys.stderr)
            return 1
    print(f"{image.name}: {image.size}/{image.imem_size} slots -> {out}")
    return 0


# ---- piolab sim / rtl -----------------------------------------------------------------------


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
    from piolab.bench.report import render_log

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
    """Let `piolab sim ... | head` end quietly instead of raising BrokenPipeError."""
    import signal

    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)


def _fail(prog: str, e: Exception) -> int:
    print(f"{prog}: {e}", file=sys.stderr)
    return 2


def sim_main(argv: Sequence[str] | None = None) -> int:
    _quiet_pipe()
    ap = argparse.ArgumentParser(prog="piolab sim",
                                 description="Run a program or scenario on the pure-Python "
                                 "bench, on the ISA pack's engine model.")
    add_run_args(ap, "program: source (any ISA pack's suffix) or image JSON")
    _add_log_args(ap)
    a = ap.parse_args(argv)

    from piolab.bench.backends.python import run_python
    from piolab.devices.spec import DeviceSpecError
    from piolab.dut import dut_factory
    from piolab.packs import UnknownModel

    opts = RunOptions.from_args(a)
    try:
        sc = opts.factory()()
        dut = dut_factory(opts.dut, sc.image, opts.model)()
    except AsmErrors as e:
        print(e.render(), file=sys.stderr)
        return 1
    except (RunError, DeviceSpecError, UnknownModel, ValueError, OSError) as e:
        return _fail("piolab sim", e)
    if sc.requires_engine and not dut.is_engine:
        print(f"note: DUT '{opts.dut}' is a stand-in; the engine program is loaded but not run",
              file=sys.stderr)
    on_event = None
    if a.trace:
        from piolab.bench.report import HEADER, format_event

        print(HEADER)
        want = set(a.source) if a.source else None

        def on_event(src: str, t: Txn) -> None:
            if want is None or src in want:
                print(format_event(src, t), flush=True)

    r = run_python(sc, dut, on_event=on_event)
    _print_result(r, a)
    vcd = opts.vcd_path(sc.name)
    if vcd:
        vcd.parent.mkdir(parents=True, exist_ok=True)
        r.waveform.to_vcd(vcd)
        print(f"  vcd: {vcd}")
    return 0


def rtl_main(argv: Sequence[str] | None = None) -> int:
    _quiet_pipe()
    ap = argparse.ArgumentParser(prog="piolab rtl",
                                 description="Run a scenario against RTL via cocotb.")
    ap.add_argument("--scenario", required=True, metavar="NAME|MOD:FN",
                    help="a name in the pack's scenarios module, or module:factory")
    ap.add_argument("--sim", default="icarus", choices=["icarus", "verilator"])
    ap.add_argument("--src", action="append", type=Path,
                    help="RTL sources (default: the packaged TT template stand-in)")
    ap.add_argument("--vcd", nargs="?", const="", metavar="FILE",
                    help="also write the bench-level waveform (no FILE: <out>/<scenario>.vcd)")
    ap.add_argument("--waves", action="store_true", help="simulator waveform dump")
    _add_log_args(ap)
    a = ap.parse_args(argv)

    from piolab.bench.backends.cocotb_rtl import run_rtl
    from piolab.run import resolve_scenario

    try:
        factory = resolve_scenario(a.scenario)
    except (RunError, ImportError) as e:
        return _fail("piolab rtl", e)
    # --trace: events are logged live inside the simulator (cocotb log, sim time prefix)
    r = run_rtl(factory, sim=a.sim, sources=a.src, waves=a.waves, trace=a.trace)
    _print_result(r, a)
    if a.vcd is not None:
        vcd = Path(a.vcd) if a.vcd else default_output(r.scenario, ".vcd")
        vcd.parent.mkdir(parents=True, exist_ok=True)
        r.waveform.to_vcd(vcd)
    return 0


def models_main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="piolab models",
                                 description="List the ISA packs and their engine models.")
    ap.parse_args(argv)
    from piolab.packs import active, packs

    act = active()
    rows = [("pack", "model", "", "factory")]
    for pack in packs().values():
        chosen = pack.model_name()
        for name, spec in pack.models.items():
            mark = "default" if name == chosen else ""
            rows.append((pack.name + (" *" if pack is act else ""), name, mark, spec))
    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip())
    print("\n* active pack. default: [tool.piolab] model if the pack has it, else the "
          "pack's default. Pick one with --model NAME.")
    return 0


# ---- piolab (dispatcher) ------------------------------------------------------------------


def _metrics(argv: Sequence[str]) -> int:
    from piolab.scripts.metrics import main as metrics_main

    return metrics_main(list(argv))


def _isa_docs(argv: Sequence[str]) -> int:
    from piolab.scripts.gen_isa_docs import main as docs_main

    return docs_main(list(argv))


def _isa_check(argv: Sequence[str]) -> int:
    from piolab.isa.check import main as check_main

    return check_main(list(argv))


def _debug(argv: Sequence[str]) -> int:
    from piolab.debug.repl import debug_main

    return debug_main(list(argv))


def _dap(argv: Sequence[str]) -> int:
    from piolab.debug.dap import dap_main

    return dap_main(list(argv))


def _vscode(argv: Sequence[str]) -> int:
    from piolab.vscode.build import main as vscode_main

    return vscode_main(list(argv))


def _new_pack(argv: Sequence[str]) -> int:
    from piolab.scripts.new_pack import main as new_pack_main

    return new_pack_main(list(argv))


def _arch(argv: Sequence[str]) -> int:
    from piolab.arch.report import main as arch_main

    return arch_main(list(argv))


COMMANDS: dict[str, tuple[Callable[[Sequence[str]], int], str]] = {
    "asm": (asm_main, "assemble a program into an image JSON / listing / hex"),
    "sim": (sim_main, "run a program or scenario on the pure-Python bench"),
    "rtl": (rtl_main, "run a scenario against RTL through cocotb"),
    "metrics": (_metrics, "per-program static ISA metrics"),
    "isa-docs": (_isa_docs, "regenerate the active pack's isa.md + isa-encoding.adoc/.html"),
    "isa-check": (_isa_check, "check the pack's yaml against its model, directives, programs "
                              "and docs"),
    "models": (models_main, "list the ISA packs and their engine models"),
    "debug": (_debug, "step through a program on its ISA pack's engine model, GDB style"),
    "dap": (_dap, "Debug Adapter Protocol server for VS Code (the `vscode` extension)"),
    "vscode": (_vscode, "build the VS Code extension (.vsix) for the installed ISA packs"),
    "new-pack": (_new_pack, "scaffold a new ISA pack project (own repo, entry-point registered)"),
    "arch": (_arch, "architecture study: PIO-style vs mesh, area and utilisation"),
}


def _pop_isa(args: list[str]) -> tuple[list[str], str | None]:
    """Remove `--isa PACK` / `--isa=PACK` from anywhere on the line."""
    out, name, i = [], None, 0
    while i < len(args):
        a = args[i]
        if a == "--isa":
            name = args[i + 1] if i + 1 < len(args) else ""
            i += 2
            continue
        if a.startswith("--isa="):
            name = a.partition("=")[2]
        else:
            out.append(a)
        i += 1
    return out, name


def main(argv: Sequence[str] | None = None) -> int:
    from piolab.config import ConfigError
    from piolab.packs import active, packs, select

    args, name = _pop_isa(list(sys.argv[1:] if argv is None else argv))
    try:
        if name is not None:
            if name not in packs():
                print(f"piolab: unknown ISA pack '{name}' (available: {', '.join(packs())})",
                      file=sys.stderr)
                return 2
            select(name)
        current = active().name
    except (ConfigError, KeyError) as e:
        print(f"piolab: {e}", file=sys.stderr)
        return 2
    usage = "piolab <command> [--isa PACK] [options]\n\ncommands:\n" + "\n".join(
        f"  {cmd:9s} {helptext}" for cmd, (_, helptext) in COMMANDS.items())
    usage += "\n\n`piolab <command> --help` shows the options of each command."
    usage += (f"\nISA packs: {', '.join(packs())} (active: {current}; "
              "--isa, $PIOLAB_ISA or [tool.piolab] pack to choose)")
    if not args or args[0] in ("-h", "--help"):
        print(f"usage: {usage}")
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    if cmd not in COMMANDS:
        print(f"piolab: unknown command '{cmd}'\nusage: {usage}", file=sys.stderr)
        return 2
    return COMMANDS[cmd][0](rest)

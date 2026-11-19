"""What to run: one description of a run, shared by `piolab sim`, `piolab debug` and
`piolab dap` (VS Code launch configurations), so the three accept the same things.

- a **program**: a path (source of any pack's suffix, or an image JSON from `piolab asm`),
  or just a program's stem (`uart_rx`), looked up in the active pack's `programs/`;
- or a **scenario**: `module:factory`, or a factory name in the active pack's
  `scenarios` module (`--scenario uart_tx`);
- **devices**: `--device uart:send=0x55` specs (`piolab.devices.spec`);
- the **DUT**: `--model NAME` (one of the pack's engine models) or `--dut` (`engine`,
  `stub`, `tt_template`, a model name, or `module:callable`).

Defaults (pack, model, cycles, device options, output dir) come from `[tool.piolab]`
(`piolab.config`).
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from piolab.bench import HostScript, Scenario, Wiring
from piolab.config import load as load_config
from piolab.image import ProgramImage


class RunError(ValueError):
    pass


# ---- programs and scenarios -----------------------------------------------------------


def resolve_program(arg: str | Path) -> Path:
    """`arg` if it is an existing file, else the active pack's program of that stem
    (`uart_rx`, `uart_rx.pio`)."""
    p = Path(arg)
    if p.is_file():
        return p
    from piolab.packs import active

    pack = active()
    if len(p.parts) == 1:
        cand = pack.program(p.name)
        if cand.is_file():
            return cand
    raise RunError(f"no program '{arg}': not a file, and not one of the '{pack.name}' pack's "
                   f"programs ({', '.join(pack.program_names()) or 'none'})")


def load_program(arg: str | Path) -> ProgramImage:
    """An image JSON from `piolab asm`, or source (assembled on the fly with the ISA pack
    of its suffix)."""
    from piolab.asm import assemble_file

    p = resolve_program(arg)
    if p.suffix == ".json":
        return ProgramImage.load_json(p)
    return assemble_file(p)


def _scenario_modules(name: str) -> list[ModuleType]:
    """`name`, and when it is a package, its submodules (not `test_*` or `_*`)."""
    import pkgutil

    mod = importlib.import_module(name)
    out = [mod]
    for m in sorted(pkgutil.iter_modules(getattr(mod, "__path__", [])), key=lambda m: m.name):
        if not m.name.startswith(("_", "test_", "conftest")):
            out.append(importlib.import_module(f"{name}.{m.name}"))
    return out


def _is_factory(v: Any, module: str) -> bool:
    """A function defined in `module` and annotated to return a Scenario."""
    ret = getattr(v, "__annotations__", {}).get("return") if callable(v) else None
    return (ret is not None and "Scenario" in str(getattr(ret, "__name__", ret))
            and getattr(v, "__module__", "") == module)


def _factories(m: ModuleType) -> list[str]:
    return [n for n, v in vars(m).items() if not n.startswith("_") and _is_factory(v, m.__name__)]


def scenario_names(module: str) -> list[str]:
    """The scenario factories defined in a scenarios module, or in a package's modules."""
    return sorted({n for m in _scenario_modules(module) for n in _factories(m)})


def resolve_scenario(ref: str) -> Callable[..., Scenario]:
    """`module:factory` (dotted attributes allowed), or a factory name in the active
    pack's `scenarios` module or package."""
    from piolab.packs import active

    mod, sep, attr = ref.partition(":")
    where = mod
    if not sep:
        pack = active()
        if not pack.scenarios:
            raise RunError(f"--scenario {ref}: give module:factory (the '{pack.name}' pack "
                           "names no scenarios module)")
        found = [m.__name__ for m in _scenario_modules(pack.scenarios)
                 if ref.split(".")[0] in _factories(m)]
        if len(found) > 1:
            raise RunError(f"--scenario {ref} is in {', '.join(found)}: give module:factory")
        where = pack.scenarios
        mod, attr = (found[0] if found else pack.scenarios), ref
    obj: Any = importlib.import_module(mod)
    for part in attr.split("."):
        if not hasattr(obj, part):
            raise RunError(f"no scenario '{attr}' in {where} "
                           f"({', '.join(scenario_names(where)) or 'none'})")
        obj = getattr(obj, part)
    if not callable(obj):
        raise RunError(f"{mod}:{attr} is not a scenario factory")
    fn: Callable[..., Scenario] = obj
    return fn


def default_output(stem: str, suffix: str) -> Path:
    """`<[tool.piolab] out>/<stem><suffix>`: where a bare `--vcd`, `--listing` go."""
    return load_config().out / f"{stem}{suffix}"


# ---- one run ----------------------------------------------------------------------------


def _words(v: Any) -> list[int]:
    if v is None or v == "":
        return []
    items = v if isinstance(v, (list, tuple)) else str(v).split(",")
    return [x if isinstance(x, int) else int(str(x).strip(), 0) for x in items]


@dataclass
class RunOptions:
    program: str | None = None
    scenario: str | None = None
    devices: list[str] = field(default_factory=list)
    cycles: int | None = None  # None: [tool.piolab] cycles
    host_tx: list[int] = field(default_factory=list)
    dut: str | None = None  # None: the engine
    model: str | None = None  # None: [tool.piolab] model, else the pack's default
    #: "" means "the default path" (`<out>/<name>.vcd`), None means no VCD.
    vcd: str | None = None

    @classmethod
    def from_args(cls, a: argparse.Namespace) -> RunOptions:
        return cls(program=a.program, scenario=a.scenario, devices=list(a.device),
                   cycles=a.cycles, host_tx=_words(a.host_tx), dut=a.dut, model=a.model,
                   vcd=getattr(a, "vcd", None))

    @classmethod
    def from_launch(cls, args: Mapping[str, Any]) -> RunOptions:
        """From a VS Code launch configuration (`.vscode/launch.json`)."""
        devs = args.get("devices", [])
        if isinstance(devs, str):
            devs = [devs]
        return cls(program=args.get("program") or None, scenario=args.get("scenario") or None,
                   devices=[str(d) for d in devs],
                   cycles=int(args["cycles"]) if args.get("cycles") else None,
                   host_tx=_words(args.get("hostTx")), dut=args.get("dut") or None,
                   model=args.get("model") or None, vcd=args.get("vcd") or None)

    def factory(self) -> Callable[[], Scenario]:
        """A zero-argument factory, so a run can be rebuilt from scratch (debugger
        restart). The program is assembled once, here, so errors show up now."""
        from piolab.devices.spec import build

        if self.scenario:
            fn = resolve_scenario(self.scenario)
            return lambda: fn()
        if self.program is None:
            raise RunError("give a program (a path or a program name) or --scenario")
        image = load_program(self.program)
        cfg = load_config()
        cycles = self.cycles or cfg.cycles
        devices, host_tx = list(self.devices), list(self.host_tx)
        build(devices, image, cfg.devices)  # validate the specs now

        def make() -> Scenario:
            host = HostScript(tx={0: list(host_tx)} if host_tx else {})
            return Scenario(image.name, Wiring.from_image(image),
                            build(devices, image, cfg.devices), cycles, image=image, host=host)

        return make

    def vcd_path(self, name: str) -> Path | None:
        if self.vcd is None:
            return None
        return Path(self.vcd) if self.vcd else default_output(name, ".vcd")


def add_run_args(ap: argparse.ArgumentParser, program_help: str) -> None:
    """The arguments of a run (`piolab sim`, `piolab debug`)."""
    from piolab.devices.spec import describe

    ap.add_argument("program", nargs="?",
                    help=f"{program_help}: a path, or a program name of the active pack "
                    "(uart_rx)")
    ap.add_argument("--scenario", metavar="NAME|MOD:FN",
                    help="a scenario factory: a name in the pack's scenarios module, or "
                    "module:factory (instead of a program and devices)")
    ap.add_argument("--device", "-d", action="append", default=[], metavar="SPEC",
                    help=f"attach a device, repeatable: {describe()}")
    ap.add_argument("--model", "-m", metavar="NAME",
                    help="engine model of the pack (`piolab models`; default: "
                    "[tool.piolab] model, else the pack's default)")
    ap.add_argument("--dut", metavar="SPEC",
                    help="instead of a model: stub | tt_template | module:callable")
    ap.add_argument("--cycles", type=int, help="cycle limit (default: [tool.piolab] cycles, "
                    "else 100000)")
    ap.add_argument("--host-tx", metavar="WORDS",
                    help="comma-separated words the host pushes to the first core's TX FIFO")
    ap.add_argument("--vcd", nargs="?", const="", metavar="FILE",
                    help="write the waveform (no FILE: <out>/<program>.vcd)")


__all__ = ["RunError", "RunOptions", "add_run_args", "default_output", "load_program",
           "resolve_program", "resolve_scenario", "scenario_names"]

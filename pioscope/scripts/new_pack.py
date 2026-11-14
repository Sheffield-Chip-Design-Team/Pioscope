"""`pioscope new-pack NAME`: write a ready-to-install ISA pack project at ./NAME/.

The skeleton is a tiny three-instruction ISA (`set`, `jmp`, `nop`) with the generic field
encoder, an engine model on `pioscope.dut.core`, the arch-study data and a blink program.
It installs as its own package and registers itself through the `pioscope.packs` entry
point, so `pioscope --isa NAME ...` finds it (ADR-0012, docs/reference/isa-flow.md §5).

    uv run pioscope new-pack myisa [--out DIR] [--pioscope PATH_OR_GIT_URL]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

PACKS = Path(__file__).resolve().parent.parent / "packs" / "pio"

PYPROJECT = '''[project]
name = "@NAME@"
version = "0.1.0"
description = "@TITLE@: an ISA pack for PIOScope"
requires-python = ">=3.12"
dependencies = ["pioscope"]

[project.entry-points."pioscope.packs"]
@NAME@ = "@PKG@.pack:PACK"

[tool.uv.sources]
@SOURCE@

[dependency-groups]
dev = ["pytest"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["@PKG@"]
artifacts = ["@PKG@/**/*.yaml", "@PKG@/programs/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
'''

PACK_PY = '''"""The @TITLE@ ISA pack: registered by the `pioscope.packs` entry point."""

from pioscope.packs import IsaPack, pack_dir

HERE = pack_dir(__file__)

PACK = IsaPack(
    name="@NAME@",
    title="@TITLE@",
    spec=HERE / "isa.yaml",
    programs=HERE / "programs",
    arch=HERE / "arch",
    model="@PKG@.model:Engine",
    demand="@PKG@.demand",
    ext=".@NAME@",
)
'''

ISA_YAML = '''# @TITLE@: a minimal ISA pack. The single source of truth for the @NAME@ pack: the
# assembler, the model's handlers and the generated docs all derive from it.
# Operand type kinds and the file structure are described in pioscope's
# pioscope/packs/pio/isa.yaml.

version: 0.1.0
name: @TITLE@
pack: @NAME@

params:
  imem_size: 32
  word_bits: 16
  delay_max: 7
  fifo_depth: 4
  sysclk_hz: 50000000    # the Tiny Tapeout default

target:
  name: tinytapeout
  pads:
    ui:  {count: 8, dir: in}
    uo:  {count: 8, dir: out}
    uio: {count: 8, dir: bidir}
  pin_modes: [push_pull, open_drain]

operand_types:
  label:    {kind: label}
  cond:     {kind: cond}
  imm5:     {kind: int, min: 0, max: 31, bits: 5}
  set_dst:  {kind: enum, values: [pins, x], codes: [0, 1]}
  name:     {kind: word}
  rest:     {kind: rest}
  pos_int:  {kind: int, min: 1, max: 1024}
  pin_map:
    kind: pattern
    regex: '^(?P<pad>(?:ui|uo|uio)[0-7])(?:/(?P<mode>push_pull|open_drain))?$'
    fields:
      pad: []
      mode: [push_pull, open_drain]
  clkdiv:
    kind: pattern
    regex: '^(?P<int>\\d+)(?:\\.(?P<frac>\\d+))?$'
    ints: [int]

conditions:             # encoded in this order
  - {name: always, semantics: "Always (no condition written)."}
  - {name: "!x",   semantics: "X is zero."}
  - {name: "x--",  semantics: "X is non-zero before the decrement; X is decremented either way."}

encoding:
  strict: false
  fields:               # MSB -> LSB; operands pack into `operand` in declaration order
    opcode:  2
    delay:   3
    operand: 11
  roles:
    opcode: "selects the instruction"
    delay: "`[n]` delay cycles"
    operand: "the instruction's arguments"

instructions:
  - name: set
    tier: 1
    opcode: 0
    operands:
      - {name: dst, type: set_dst}
      - {name: value, type: imm5}
    semantics: "Write a 5-bit immediate to the output pins (bit n drives output role n) or to X."
  - name: jmp
    tier: 1
    opcode: 1
    operands:
      - {name: cond, type: cond, optional: true, default: always}
      - {name: target, type: label}
    semantics: "If cond is true, jump to target. x-- decrements X either way."
  - name: nop
    tier: 1
    opcode: 2
    operands: []
    semantics: "No effect beyond the delay."

default_modifiers: [delay]

directives:
  - name: .program
    operands:
      - {name: name, type: name}
    semantics: "Start of a program. One per source file."
  - name: .pins
    operands:
      - {name: map, type: rest}
    semantics: "Name the pads the program uses: `.pins led=uo0 sda=uio1/open_drain`."
  - name: .fifo
    operands:
      - {name: tx, type: pos_int, key: tx, optional: true, default: 4}
      - {name: rx, type: pos_int, key: rx, optional: true, default: 4}
    semantics: "FIFO depths."
  - name: .clkdiv
    operands:
      - {name: div, type: clkdiv}
    semantics: "Core clock divider, int[.frac]. Default 1 (every sysclk)."
  - name: .wrap_target
    operands: []
    semantics: "Address the program wraps back to."
  - name: .wrap
    operands: []
    semantics: "After the preceding instruction, continue at .wrap_target at no cost."
  - name: .define
    operands:
      - {name: name, type: name}
      - {name: value, type: rest}
    semantics: "Textual constant substituted for later operand items."
'''

MODEL_PY = '''"""The @TITLE@ engine model on the generic core (`pioscope.dut.core`): one handler per
instruction of isa.yaml. `registry()` builds the table; the pack's `model=` names `Engine`.

A handler gets (core, instruction, first) on each core cycle the instruction is current,
and returns True when it completes or False to stall (set `core.op["stall"]` to say why).
Model it further on pioscope/dut/pio/.
"""

from __future__ import annotations

from pioscope.dut.core import M32, CoreConfig, EngineError, registry
from pioscope.dut.core import Core as BaseCore
from pioscope.dut.core import Engine as BaseEngine
from pioscope.image import IrInstr, ProgramImage

HANDLERS, handler = registry()


class Core(BaseCore):
    """One core of the @NAME@ engine. Add registers, flags and units here."""


@handler("set")
def _set(core: Core, ins: IrInstr, first: bool) -> bool:
    value = int(ins.operands["value"])
    if ins.operands["dst"] == "pins":
        core.set_out_bits(value)
    else:
        core.x = value
    return True


def _cond(core: Core, cond: str) -> bool:
    if cond == "always":
        return True
    if cond == "!x":
        return core.x == 0
    if cond == "x--":
        taken = core.x != 0
        core.x = (core.x - 1) & M32
        return taken
    raise EngineError(f"unknown condition '{cond}'", core.src)


@handler("jmp")
def _jmp(core: Core, ins: IrInstr, first: bool) -> bool:
    if _cond(core, ins.operands["cond"]):
        core.pc, core.jumped = int(ins.operands["target"]), True
    return True


@handler("nop")
def _nop(core: Core, ins: IrInstr, first: bool) -> bool:
    return True


class Engine(BaseEngine):
    """The DUT: one core running the program on the Tiny Tapeout pads."""

    def build(self, image: ProgramImage) -> None:
        cfg = CoreConfig.from_image(image)
        self.cores = [Core(image, cfg, HANDLERS, self.fs, "c0")]
'''

BLINK = '''; Blink an LED: 8 cycles high, 8 cycles low. The delay `[7]` is on top of the
; instruction's own cycle.
.program blink
.pins led=uo0

.wrap_target
        set pins, 1 [7]
        set pins, 0 [7]
.wrap
'''

WORKLOADS = '''# Protocol mixes for the @TITLE@ architecture study (docs/arch-guide.md).
# `prog` names a program of this pack (@PKG@/programs, without the suffix); `load` is
# the fraction of time its line is busy (0..1).

workloads:
  - name: blinker
    description: one blinking LED
    programs:
      - {prog: blink, load: 0.5}
  - name: blink_pair
    description: two LEDs blinking side by side
    programs:
      - {prog: blink, load: 0.5}
      - {prog: blink, load: 0.5}
'''

CONFTEST = '''"""Activate the @NAME@ pack for these tests.

The bench fixtures (`bench`, `golden`, `--dut`) come from PIOScope's pytest plugin."""

from collections.abc import Iterator

import pytest

from pioscope.packs import IsaPack, use


@pytest.fixture(autouse=True)
def pack() -> Iterator[IsaPack]:
    with use("@NAME@") as p:
        yield p
'''

TEST_PACK = '''"""The @NAME@ pack assembles, runs, and implements every instruction of its yaml."""

from pioscope.asm import assemble_file
from pioscope.bench.pads import PadIn
from pioscope.isa import load_isa
from pioscope.packs import get
from @PKG@.model import HANDLERS, Engine

PACK = get("@NAME@")


def test_every_instruction_has_a_handler() -> None:
    assert set(HANDLERS) == set(load_isa(PACK.spec).instructions)


def test_blink_toggles_the_led() -> None:
    image = assemble_file(PACK.program("blink"))
    dut = Engine()
    dut.load(image)
    dut.reset()
    levels = [dut.step(PadIn()).uo & 1 for _ in range(40)]
    assert levels[:8] == [1] * 8 and levels[8:16] == [0] * 8
    assert levels[16:24] == [1] * 8
'''


def render(text: str, name: str) -> str:
    title = name.replace("_", " ").upper()
    return (text.replace("@NAME@", name).replace("@PKG@", f"{name}_isa")
            .replace("@TITLE@", title))


def valid_name(name: str) -> bool:
    return re.fullmatch(r"[a-z][a-z0-9_]*", name) is not None


def scaffold(name: str, out: Path, pioscope: str | None = None) -> Path:
    """Write the project for pack `name` under `out`/`name`; return its directory."""
    if not valid_name(name):
        raise ValueError(f"'{name}' is not a valid pack name: use lower-case letters, "
                         "digits and underscores, starting with a letter")
    root = out / name
    if root.exists():
        raise FileExistsError(f"{root} already exists")
    pkg = root / f"{name}_isa"
    if pioscope is None:
        checkout = Path(__file__).resolve().parents[2]  # a source checkout of pioscope?
        if (checkout / "pyproject.toml").is_file() and (checkout / "pioscope").is_dir():
            pioscope = os.path.relpath(checkout, root)
    if pioscope is None:
        source = ('# pioscope = { path = "../pioscope", editable = true }   # co-development\n'
                  '# pioscope = { git = "https://github.com/Sheffield-Chip-Design-Team/Pioscope", tag = "v0.2.0" }')
    elif "://" in pioscope or pioscope.startswith("git@"):
        source = f'pioscope = {{ git = "{pioscope}" }}'
    elif pioscope.endswith(".whl"):
        source = f'pioscope = {{ path = "{pioscope}" }}'
    else:
        source = f'pioscope = {{ path = "{pioscope}", editable = true }}'
    files: dict[Path, str] = {
        root / "pyproject.toml": PYPROJECT.replace("@SOURCE@", source),
        pkg / "__init__.py": "",
        pkg / "pack.py": PACK_PY,
        pkg / "isa.yaml": ISA_YAML,
        pkg / "model.py": MODEL_PY,
        pkg / "programs" / f"blink.{name}": BLINK,
        pkg / "arch" / "workloads.yaml": WORKLOADS,
        root / "tests" / "__init__.py": "",
        root / "tests" / "conftest.py": CONFTEST,
        root / "tests" / "test_pack.py": TEST_PACK,
        root / ".python-version": "3.13\n",  # cocotb (a pioscope dependency) needs <= 3.13
        root / ".gitignore": ".venv/\n__pycache__/\n*.egg-info/\nbuild/\ndist/\n",
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(text, name))
    # Unchanged from the PIO pack: the area model, the architectures and the demand hooks.
    for f in ("cost.yaml", "architectures.yaml"):
        (pkg / "arch" / f).write_text((PACKS / "arch" / f).read_text())
    (pkg / "demand.py").write_text(render(DEMAND_PY, name))
    return root


DEMAND_PY = '''"""@TITLE@ arch-study hooks (ADR-0012): a program's demand besides instruction slots.

This skeleton has no units and no FIFO instructions, so the demand is empty."""

from __future__ import annotations

from pioscope.image import ProgramImage


def units_used(image: ProgramImage) -> list[str]:
    return []


def bit_shares(image: ProgramImage, units: frozenset[str]) -> dict[str, float]:
    return {}


def fifos(image: ProgramImage) -> tuple[bool, bool]:
    """(uses a TX FIFO, uses an RX FIFO)."""
    return False, False


def bit_rate(image: ProgramImage) -> float | None:
    return None
'''


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pioscope new-pack", description=__doc__.split("\n\n")[0])
    ap.add_argument("name", help="pack name: lower-case, e.g. myisa (source suffix .myisa)")
    ap.add_argument("--out", type=Path, default=Path("."), help="parent directory (default: .)")
    ap.add_argument("--pioscope", metavar="PATH_OR_URL",
                    help="where uv finds pioscope: a local path (editable) or a git URL "
                    "(default: the checkout this pioscope runs from, if any)")
    args = ap.parse_args(argv)
    try:
        root = scaffold(args.name, args.out, args.pioscope)
    except (ValueError, FileExistsError) as e:
        print(f"pioscope new-pack: {e}", file=sys.stderr)
        return 1
    print(f"wrote {root}\nnext: cd {root} && uv sync && uv run pytest && "
          f"uv run pioscope --isa {args.name} isa-docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

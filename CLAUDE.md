# PIOLab: ISA packs, assembler, engine models, bench and debugger for programmable IO

This repository is a pre-RTL toolkit for PIO-style programmable IO engines. Its purpose is to **design and validate an instruction set** for serial protocols with a Python assembler and cycle-accurate simulator, before any RTL is written.

The deliverable is **evidence that an ISA is suitable**: every target protocol runs correctly, timing meets spec, and the cost of each one is known (instructions, cycles per bit, area). Gaps found in an ISA are a successful outcome, not a failure.

---

## 1. Background

A PIO-style engine has small cores (state machines, on the RP2040) that run short programs and move bits between FIFOs and pins with exact timing. Almost every serial protocol is **structured data ↔ a timed stream of symbols on pins**. The questions here are how cheaply an instruction set expresses that, and how the engine should be organised:
- private resources per core (PIO), or
- pools allocated by software (a mesh).

An instruction set is an **ISA pack** (ADR-0012). The built-in **demonstration pack** is the **RP2040 PIO** (`piolab/packs/pio/`: `isa.yaml`, hooks, `models/base/`, programs, tests), laid out the way a project's own pack is:
- the exact 16-bit encoding;
- a state-machine model;
- UART, SPI, I²C and WS2812 programs.

New packs are added the same way (docs/reference/isa-flow.md §5).

---

## 2. Scope

**In scope:**
1. ISA specifications, each in a single machine-readable source (`<pack>/isa.yaml`).
2. An assembler: source → instruction words + a config image + a listing.
3. Cycle-accurate, deterministic engine models (one or more cores) with pins, FIFOs and units, on a shared core (`piolab/dut/core`).
4. Device models (the other end of each bus) and independent waveform decoders.
5. Protocol programs and a test suite that validates them.
6. Per-protocol metrics, and an analytic architecture study (area, utilisation).
7. Modelling runtime reconfiguration and contention (shared units, arbiters, a controller; ADR-0011, proposed).

**Out of scope (for now):**
- RTL, synthesis, timing closure;
- a high-level protocol language or compiler above assembly;
- performance of the simulator: correctness and clarity come first.

---

## 3. Machine model (the generic core)

**Per core** (`piolab.dut.core.Core`):
- a PC and instruction memory (32 words like PIO; a parameter of the ISA);
- scratch registers X and Y (32-bit);
- shift registers OSR and ISR, 32-bit, with shift counters;
- TX and RX FIFOs, depth configurable (default 4);
- flags, as an ISA defines them;
- a clock: system clock through an integer/fractional divider, as in PIO.

A model adds what its ISA needs: autopull/autopush and shift directions (PIO), units, timed operations.

**Units:**
- hardware attached to a core, or shared between cores (`piolab.dut.core.Unit`);
- the PIO model's GPIO outputs and IRQ flags are shared units.

The tools (trace, VCD, debugger) show any unit a model reports, through the observability contract (ADR-0010).

**Pins:**
- Each pin has a value, an output-enable and a mode: `push_pull` or `open_drain`.
- The bench resolves shared buses. Open-drain lines are wired-AND with a pull-up. Two push-pull drivers that disagree are a **simulation error**, and it is reported.
- Inputs go through a 2-flop synchroniser, and the latency is modelled.

**Configuration:** directives in source become a **config image**, applied before the program runs. Directives a C SDK would normally provide are marked `harness: true` in the yaml.

---

## 4. Validation approach

| Protocol | Must demonstrate |
|---|---|
| UART | TX/RX 8N1; framing error; tolerance to baud error |
| SPI | modes 0–3; full duplex; MSB/LSB first |
| I²C | master write; ACK/NACK; clock stretching |

**Test structure:**
1. **Device models** in `piolab/devices/` play the other end of the bus: a UART partner (configurable baud error and glitches), an SPI slave (all modes), an I²C slave (address match, stretching, NACK), and a second I²C master.
2. **Independent reference decoders** in `piolab/decoders/` decode the pin waveform. They must not share code with the engine models.
3. **Property-based tests** (Hypothesis): random payloads round-trip, encodings round-trip.
4. **Fault injection:** baud error, glitches, NACK, stretching.
5. **Timing checks** against each protocol's spec (bit period, setup/hold, sample point).

---

## 5. Tooling and layout

- **Python 3.12 or 3.13** (cocotb does not support 3.14 yet), `uv` for the environment and dependencies, `pytest` + `hypothesis`, `ruff`, `mypy --strict`.
- Waveforms: `pyvcd` for VCD output (view in GTKWave or Surfer).
- Pure Python; no compiled extensions.

```
CLAUDE.md
docs/
  README.md                # documentation map: start here
  tutorials/               # hands-on: first program, device+decoder, probe, unit, own pack + models, ISA change
  requirements/units/      # unit requirement sheet template
  debugging.md             # guide: trace events, VCD + GTKWave, piolab debug, VS Code
  reference/               # simulator.md, isa-flow.md, debugger.md: how the code works
  bench.md                 # how the verification bench works
  arch-guide.md            # guide to the architecture study
  open_items/              # ADRs: one file per decision
  pio/                     # the PIO pack: machine-model.md + generated isa.md, isa-encoding.*, arch-study.*
piolab/                    # the Python package (installable; everything below ships in the wheel)
  isa/                     # loads a pack's yaml; check.py: `piolab isa-check`
  asm/                     # lexer, operands, directives, encoder (hook), expressions, listing, errors
  image.py                 # ProgramImage (IR + words + config + pack) <-> JSON / hex
  wave.py                  # Waveform (the only module decoders may import)
  bench/                   # board, nets, pads, wiring, scenarios, loaders; backends/ python + cocotb_rtl
    rtl/                   # the RTL harness tb.v and the Tiny Tapeout template stand-in (package data)
  dut/                     # DUT contract + stand-ins (dut_factory: engine | model name | mod:fn); core/ (the generic engine)
  debug/                   # piolab debug (GDB-style REPL) and piolab dap (VS Code)
  devices/                 # bus partner models; spec.py: `--device uart:baud=N,send=..` specs
  decoders/                # independent waveform decoders
  checks/                  # scoreboard + spec timing checks
  packs/                   # pack discovery (built-ins + the `piolab.packs` entry points), discover_models
    pio/                   # the PIO demo pack: isa.yaml, models/base/ (the state-machine model),
                           #   arch/ (workloads, architectures, costs), programs/,
                           #   hooks (directives, encode, demand), tests/
  examples/                # toy examples used by the tutorials
  arch/                    # architecture study: demand, allocators, area model, report, viz page
  scripts/                 # metrics, ISA doc generators, new_pack (the `new-pack` scaffold)
  vscode/                  # VS Code extension; `piolab vscode` builds it for the installed packs
  testing.py               # pytest plugin (`pytest11`): bench, golden, --backend, --model, --dut, ...
  config.py                # `[tool.piolab]` project defaults (pack, model, cycles, out, docs, devices)
  run.py                   # RunOptions: program/scenario/devices/model, shared by sim, debug, dap
  cli.py                   # the `piolab` command
  tests/                   # framework tests
```

Other packs live in their own packages and register through the `piolab.packs` entry point (`piolab new-pack NAME`). A pack's models are the sub-packages of `<pkg>/models/` (`discover_models`), each exporting `Engine` and `HANDLERS`. A project sets its defaults in `[tool.piolab]` (docs/reference/isa-flow.md §5, ADR-0015). Generated documents go to `docs/<pack>` under the project directory. The Tiny Tapeout project template is the separate `pioscope-tt-template` repo.

### CLI
- `piolab asm PROG [-o F] [--listing [F]] [--hex DIR]`: instruction words + config image + listing. `PROG` is a path (the suffix picks the pack) or a program name of the active pack (`uart_tx`); outputs default to `<out>/<name>.*`.
- `piolab sim PROG|IMAGE.json [-m MODEL] [-d uart:baud=115200,send=0x55] [--vcd [F]] [--trace]`: run on the pack's engine model with devices attached (`--scenario NAME|mod:fn` instead of a program; `--dut stub|tt_template|mod:fn` for a stand-in).
- `piolab debug PROG -d uart`: GDB-style stepping, same run options as `sim`. `piolab dap` backs the VS Code extension (`docs/debugging.md`).
- `piolab isa-check [--all] [-v]`: checks the pack's yaml against its model's handlers, directive handlers, semantics text, programs, directed tests and generated docs (agreements #1 and #6); exit 1 on a gap. The pack tests run the same checks (`piolab.isa.check`).
- `piolab rtl --scenario mod:factory` (cocotb), `piolab metrics`, `piolab isa-docs`, `piolab arch [--write]`. `piolab vscode` builds the VS Code extension; `piolab new-pack NAME` scaffolds a new ISA pack project. `piolab models` lists the packs' models. `piolab --help` lists all; `--isa PACK` (anywhere) picks the pack.

---

## 6. Working agreements

1. **A pack's `<pack>/isa.yaml` is the single source of truth** for its ISA. The assembler, the model's set of handlers and the docs all derive from it. Pack-specific code is named by its `hooks:`. Never hard-code an instruction in two places.
2. **Don't silently change ISA semantics.** If a change is needed, write a short ADR in `docs/open_items/` first, then change the yaml. A pack that models an existing ISA (PIO) changes only to match it more closely.
3. **Don't add an instruction just to make a test pass.** A protocol that needs a new instruction is a *finding*. Record it first.
4. **Deterministic, cycle-accurate simulation.** No wall-clock time, no unseeded randomness. Every cycle's state must be reproducible.
5. **RTL-friendly semantics.** Every instruction does bounded work per cycle, bit widths are explicit, and no behaviour would need unbounded hardware.
6. **Every instruction has three things:** spec text in its yaml, a model implementation, and at least one directed test.
7. **Assembler errors must be good:** file, line, column, what was expected, and a hint.
8. Prefer small, reviewable commits.

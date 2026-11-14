# PIOScope: ISA packs, assembler, engine models, bench and debugger for programmable IO

This repository is a pre-RTL toolkit for PIO-style programmable IO engines. Its purpose is to **design and validate an instruction set** for serial protocols with a Python assembler and cycle-accurate simulator, before any RTL is written.

The deliverable is **evidence that an ISA is suitable**: every target protocol runs correctly, timing meets spec, and the cost of each one is known (instructions, cycles per bit, area). Gaps found in an ISA are a successful outcome, not a failure.

---

## 1. Background

A PIO-style engine has small cores (state machines, on the RP2040) that run short programs and move bits between FIFOs and pins with exact timing. Almost every serial protocol is **structured data ↔ a timed stream of symbols on pins**. The questions here are how cheaply an instruction set expresses that, and how the engine should be organised:
- private resources per core (PIO), or
- pools allocated by software (a mesh).

An instruction set is an **ISA pack** (ADR-0012). The example pack is the **RP2040 PIO** (`pioscope/packs/pio/isa.yaml`, `pioscope/packs/pio`, `pioscope/dut/pio`):
- the exact 16-bit encoding;
- a state-machine model;
- UART, SPI, I²C and WS2812 programs.

New packs are added the same way (docs/reference/isa-flow.md §5).

---

## 2. Scope

**In scope:**
1. ISA specifications, each in a single machine-readable source (`<pack>/isa.yaml`).
2. An assembler: source → instruction words + a config image + a listing.
3. Cycle-accurate, deterministic engine models (one or more cores) with pins, FIFOs and units, on a shared core (`pioscope/dut/core`).
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

**Per core** (`pioscope.dut.core.Core`):
- a PC and instruction memory (32 words like PIO; a parameter of the ISA);
- scratch registers X and Y (32-bit);
- shift registers OSR and ISR, 32-bit, with shift counters;
- TX and RX FIFOs, depth configurable (default 4);
- flags, as an ISA defines them;
- a clock: system clock through an integer/fractional divider, as in PIO.

A model adds what its ISA needs: autopull/autopush and shift directions (PIO), units, timed operations.

**Units:**
- hardware attached to a core, or shared between cores (`pioscope.dut.core.Unit`);
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
1. **Device models** in `pioscope/devices/` play the other end of the bus: a UART partner (configurable baud error and glitches), an SPI slave (all modes), an I²C slave (address match, stretching, NACK), and a second I²C master.
2. **Independent reference decoders** in `pioscope/decoders/` decode the pin waveform. They must not share code with the engine models.
3. **Property-based tests** (Hypothesis): random payloads round-trip, encodings round-trip.
4. **Fault injection:** baud error, glitches, NACK, stretching.
5. **Timing checks** against each protocol's spec (bit period, setup/hold, sample point).

---

## 5. Tooling and layout

- **Python 3.12+**, `uv` for the environment and dependencies, `pytest` + `hypothesis`, `ruff`, `mypy --strict`.
- Waveforms: `pyvcd` for VCD output (view in GTKWave or Surfer).
- Pure Python; no compiled extensions.

```
CLAUDE.md
<pack>/isa.yaml       # single source of truth for each ISA (pio.yaml: RP2040 PIO)
<pack>/arch/          # architecture study inputs: workloads, architectures, area costs
docs/
  README.md                # documentation map: start here
  tutorials/               # hands-on: first program, device+decoder, probe, unit, ISA change
  requirements/units/      # unit requirement sheet template
  debugging.md             # guide: trace events, VCD + GTKWave, pioscope debug, VS Code
  reference/               # simulator.md, isa-flow.md, debugger.md: how the code works
  bench.md                 # how the verification bench works
  arch-guide.md            # guide to the architecture study
  open_items/              # ADRs: one file per decision
  pio/                     # the PIO pack: machine-model.md + generated isa.md, isa-encoding.*, arch-study.*
pioscope/                       # Python package
  isa/                     # loads a pack's yaml
  asm/                     # lexer, operands, directives, encoder (hook), expressions, listing, errors
  image.py                 # ProgramImage (IR + words + config + pack) <-> JSON / hex
  wave.py                  # Waveform (the only module decoders may import)
  bench/                   # board, nets, pads, wiring, scenarios, loaders; backends/ python + cocotb_rtl
  dut/                     # DUT contract + stand-ins; core/ (the generic engine); pio/ (the PIO model)
  debug/                   # pioscope debug (GDB-style REPL) and pioscope dap (VS Code)
  devices/                 # bus partner models
  decoders/                # independent waveform decoders
  checks/                  # scoreboard + spec timing checks
  packs/                   # ISA packs: pio/ (hooks, programs/, tests/)
  examples/                # toy examples used by the tutorials
  arch/                    # architecture study: demand, allocators, area model, report
  scripts/                 # metrics, ISA doc generators
  cli.py                   # the `pioscope` command
  tests/                   # framework tests
test/                      # Tiny Tapeout cocotb flow (tb.v, test.py, Makefile)
pioscope/vscode/       # VS Code extension: highlighting for every pack + the debugger
```

### CLI
- `pioscope asm prog.pio -o prog.json --listing prog.lst [--hex DIR]`: instruction words + config image + listing. The suffix picks the pack.
- `pioscope sim prog.pio|prog.json --dut engine --device uart_partner --baud 115200 --vcd out.vcd [--trace]`: run with a device model attached.
- `pioscope debug prog.pio --device uart_partner --baud 115200`: GDB-style stepping. `pioscope dap` backs the VS Code extension (`docs/debugging.md`).
- `pioscope rtl --scenario mod:factory` (cocotb), `pioscope metrics`, `pioscope isa-docs`, `pioscope arch [--write]`. `pioscope --help` lists all, and `pioscope --isa PACK …` picks the pack.

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

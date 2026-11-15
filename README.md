![](../../workflows/ci/badge.svg)

# PIO-Scope: a framework for designing programmable-IO instruction sets

PIOScope is a pre-RTL toolkit for PIO-style programmable IO engines, like the RP2040's PIO. You describe an instruction set in one yaml file, and you get:
- an **assembler** with good errors;
- a **pin-timing-accurate engine model**;
- a **verification bench** with bus device models and independent waveform decoders;
- a GDB-style and **VS Code debugger**;
- waveforms;
- an **architecture study** that sizes and compares engine organisations on area and utilisation.

The question it answers is whether an instruction set suits real serial protocols, before any RTL is written: correct, on time, and at a sensible cost.

An instruction set is an **ISA pack** ([ISA PACKS EXPLAINED](docs/open_items/0012-isa-packs.md)). The example pack is the **RP2040 PIO**:
- `pioscope/packs/pio/isa.yaml`: the nine instructions in pioasm syntax;
- the exact 16-bit encoding (words match pioasm);
- a state-machine model with autopull/autopush, shift directions, `exec`, FIFO joins and IRQ flags;
- UART, SPI (modes 0–3), I²C and WS2812 programs that pass against the device models and decoders.

## What's in it

| Area | What it does | Read |
|---|---|---|
| **ISA packs** | spec yaml + optional hooks (directives, binary encoder) + engine model + programs + arch data; the framework names no instruction | [isa-packs.md](docs/open_items/0012-isa-packs.md), [isa-flow.md](docs/reference/isa-flow.md) |
| **Assembler** | table-driven from the yaml; `.define` and integer expressions; listings; errors with file:line:column, what was expected and a hint | [isa-flow.md](docs/reference/isa-flow.md), [pio/isa.md](docs/pio/isa.md), [pio/isa-encoding.html](docs/pio/isa-encoding.html) |
| **Engine core + PIO model** | registers, FIFOs, pins, side-set, delay, wrap, fractional clock divider, units; one handler per instruction | [simulator.md](docs/reference/simulator.md), [pio/machine-model.md](docs/pio/machine-model.md) |
| **Verification bench** | nets with wired-AND and contention checks, device models (UART, SPI, I²C master/slave), independent decoders, async device clocks, timing checks, a Python backend and a cocotb/RTL backend with proven parity | [bench.md](docs/bench.md) |
| **Trace** | every instruction, stall, FIFO transfer and device event, with exact time | [debugging.md §1](docs/debugging.md#1-trace-events) |
| **Waveforms** | VCD of the bus nets plus every register and unit signal, grouped per core and unit, with a generated GTKWave layout | [debugging.md §2](docs/debugging.md#2-waveforms-vcd) |
| **Step debugger** | `pioscope debug` (GDB-style) and a **VS Code debugger** (`pioscope/vscode`): breakpoints, step by instruction or by clock cycle, watchpoints, event catchpoints, disassembly, editable registers, the waveform rewritten at every stop | [debugging.md §3–5](docs/debugging.md#3-the-terminal-debugger-name-debug) |
| **The tools follow the model** | add a core, a unit or a shared unit to a model and it appears in the debugger, the trace and the VCD with no tool changes | [ADR-0010](docs/open_items/0010-cores-and-observability.md) |
| **Architecture study** | PIO-style vs a software-allocated mesh: smallest configuration per family, area (IHP SG13G2 cells and SRAM macros), utilisation, sensitivity, an interactive page | [arch-guide.md](docs/arch-guide.md), [pio/arch-study.html](docs/pio/arch-study.html) |

## Quick start

```bash
uv sync && uv run pytest                      # environment + test suite
uv run pioscope --help                          # asm, sim, debug, dap, rtl, metrics, isa-docs, isa-check, arch, vscode, new-pack
uv run pioscope isa-check -v                    # yaml vs model handlers, directives, programs, tests, docs
uv run pioscope asm pioscope/packs/pio/programs/uart_tx.pio --listing build/uart_tx.lst
uv run pioscope sim pioscope/packs/pio/programs/uart_rx.pio --dut engine --device uart_partner \
    --baud 115200 --uart-send 0x55 --trace --vcd build/rx.vcd
uv run pioscope debug pioscope/packs/pio/programs/uart_rx.pio --device uart_partner --baud 115200 \
    --uart-send 0x55
gtkwave build/rx.vcd &
```

VS Code: build and install the extension once (`uv run pioscope vscode && code --install-extension build/pioscope-asm-0.1.0.vsix`), reload the window, open a `.pio` file and press F5. See [debugging.md §0](docs/debugging.md#0-setup-once).

**Start here:** [docs/README.md](docs/README.md) (the documentation map), then [tutorials 01–04](docs/tutorials/).

## Use it in your project

PIOScope is a library: your ISA lives in **your own repository** and PIOScope finds it through an entry point. You don't fork PIOScope to add an ISA.

```bash
uv init myproject && cd myproject
uv add "pioscope @ git+https://github.com/Sheffield-Chip-Design-Team/Pioscope@v0.2.0"   # until it is on PyPI
uv run pioscope new-pack myisa       # writes ./myisa/: a ready-to-install pack project
cd myisa && uv sync && uv run pytest
uv run pioscope --isa myisa isa-docs && uv run pioscope --isa myisa arch --write
```

- **What `new-pack` writes.** A three-instruction ISA (`isa.yaml`), an engine model on `pioscope.dut.core`, the arch-study data, a `blink` program and tests. It is registered by `[project.entry-points."pioscope.packs"]`. Grow it from there ([isa-flow.md §5](docs/reference/isa-flow.md#5-adding-an-isa-pack)). Add directive or encoder hooks only where the yaml can't say it. The assembler, bench, debugger, waveforms, VS Code highlighting and arch study then work for it unchanged.
- **Co-developing PIOScope and your pack.** Use an editable path instead of the git source: `[tool.uv.sources] pioscope = { path = "../pioscope", editable = true }`. `new-pack` writes this line itself when it runs from a PIOScope checkout, or pass `--pioscope PATH_OR_URL`. Pin a tag in CI.
- **Tests.** The pytest plugin is installed with PIOScope, so any project gets the `bench` and `golden` fixtures and the `--backend`, `--dut`, `--waves` and `--update-golden` options without a conftest.
- **Python.** 3.12 or 3.13. cocotb does not support 3.14 yet.
- **A tapeout.** Start from the `pioscope-tt-template` repository (below).

## Contributing

1. Branch from `main`: `git switch -c <area>/<topic>`.
2. Make small commits. Keep `uv run pytest`, `uv run ruff check pioscope` and `uv run mypy` clean, and regenerate generated files (`pioscope isa-docs`, `pioscope arch --write`, `pytest --update-golden`) instead of hand-editing them.
3. Open a pull request. ISA changes need an ADR first ([isa-flow.md §4](docs/reference/isa-flow.md#4-checklists)). The working rules are in [CLAUDE.md](CLAUDE.md).

---

## Tiny Tapeout

The bench models a [Tiny Tapeout](https://tinytapeout.com) tile: the DUT's pads are its `ui`/`uo`/`uio` pins. The RTL backend drives any `tt_um_*` top through the packaged harness (`pioscope/bench/rtl/tb.v`).

For a whole Tiny Tapeout project, start from the **`pioscope-tt-template`** repository. It is the Tiny Tapeout IHP Verilog template (GDS, FPGA, docs and test actions, the cocotb flow), with PIOScope as its bench.

## License

Apache-2.0, see [LICENSE](LICENSE). The RP2040 PIO instruction set is Raspberry Pi Ltd's; this project models it from the public RP2040 datasheet and is not affiliated with Raspberry Pi.

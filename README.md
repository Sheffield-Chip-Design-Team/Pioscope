![](../../workflows/gds/badge.svg) ![](../../workflows/docs/badge.svg) ![](../../workflows/test/badge.svg) ![](../../workflows/fpga/badge.svg)

# PIOScope: a framework for designing programmable-IO instruction sets

PIOScope is a pre-RTL toolkit for PIO-style programmable IO engines, like the RP2040's PIO. You describe an instruction set in one yaml file, and you get:
- an **assembler** with good errors;
- a **cycle-accurate engine model**;
- a **verification bench** with bus device models and independent waveform decoders;
- a GDB-style and **VS Code debugger**;
- waveforms;
- an **architecture study** that sizes and compares engine organisations on area and utilisation.

The question it answers is whether an instruction set suits real serial protocols, before any RTL is written: correct, on time, and at a sensible cost.

An instruction set is an **ISA pack** ([ADR-0012](docs/open_items/0012-isa-packs.md)). The example pack is the **RP2040 PIO**:
- `spec/isa/pio.yaml`: the nine instructions in pioasm syntax;
- the exact 16-bit encoding (words match pioasm);
- a state-machine model with autopull/autopush, shift directions, `exec`, FIFO joins and IRQ flags;
- UART, SPI (modes 0–3), I²C and WS2812 programs that pass against the device models and decoders.

## What's in it

| Area | What it does | Read |
|---|---|---|
| **ISA packs** | spec yaml + optional hooks (directives, binary encoder) + engine model + programs + arch data; the framework names no instruction | [ADR-0012](docs/open_items/0012-isa-packs.md), [isa-flow.md](docs/reference/isa-flow.md) |
| **Assembler** | table-driven from the yaml; `.define` and integer expressions; listings; errors with file:line:column, what was expected and a hint | [isa-flow.md](docs/reference/isa-flow.md), [pio/isa.md](docs/pio/isa.md), [pio/isa-encoding.html](docs/pio/isa-encoding.html) |
| **Engine core + PIO model** | registers, FIFOs, pins, side-set, delay, wrap, fractional clock divider, units; one handler per instruction | [simulator.md](docs/reference/simulator.md), [pio/machine-model.md](docs/pio/machine-model.md) |
| **Verification bench** | nets with wired-AND and contention checks, device models (UART, SPI, I²C master/slave), independent decoders, async device clocks, timing checks, a Python backend and a cocotb/RTL backend with proven parity | [bench.md](docs/bench.md) |
| **Trace** | every instruction, stall, FIFO transfer and device event, with exact time | [debugging.md §1](docs/debugging.md#1-trace-events) |
| **Waveforms** | VCD of the bus nets plus every register and unit signal, grouped per core and unit, with a generated GTKWave layout | [debugging.md §2](docs/debugging.md#2-waveforms-vcd) |
| **Step debugger** | `pioscope debug` (GDB-style) and a **VS Code debugger** (`tools/vscode-pioscope`): breakpoints, step by instruction or by clock cycle, watchpoints, event catchpoints, disassembly, editable registers, the waveform rewritten at every stop | [debugging.md §3–5](docs/debugging.md#3-the-terminal-debugger-name-debug) |
| **The tools follow the model** | add a core, a unit or a shared unit to a model and it appears in the debugger, the trace and the VCD with no tool changes | [ADR-0010](docs/open_items/0010-cores-and-observability.md) |
| **Architecture study** | PIO-style vs a software-allocated mesh: smallest configuration per family, area (IHP SG13G2 cells and SRAM macros), utilisation, sensitivity, an interactive page | [arch-guide.md](docs/arch-guide.md), [pio/arch-study.html](docs/pio/arch-study.html) |

## Quick start

```bash
uv sync && uv run pytest                      # environment + test suite
uv run pioscope --help                          # asm, sim, debug, dap, rtl, metrics, isa-docs, arch
uv run pioscope asm pioscope/packs/pio/programs/uart_tx.pio --listing build/uart_tx.lst
uv run pioscope sim pioscope/packs/pio/programs/uart_rx.pio --dut engine --device uart_partner \
    --baud 115200 --uart-send 0x55 --trace --vcd build/rx.vcd
uv run pioscope debug pioscope/packs/pio/programs/uart_rx.pio --device uart_partner --baud 115200 \
    --uart-send 0x55
gtkwave build/rx.vcd &
```

VS Code: build and install the extension once (`uv run python tools/vscode-pioscope/build.py && code --install-extension tools/vscode-pioscope/pioscope-asm-0.1.0.vsix`), reload the window, open a `.pio` file and press F5. See [debugging.md §0](docs/debugging.md#0-setup-once).

**Start here:** [docs/README.md](docs/README.md) (the documentation map), then [tutorials 01–04](docs/tutorials/).

## Adding your own ISA

Copy the PIO pack's structure ([isa-flow.md §5](docs/reference/isa-flow.md#5-adding-an-isa-pack)):
1. write a yaml spec;
2. add directive or encoder hooks only where yaml can't say it;
3. build an engine model on `pioscope.dut.core`;
4. write programs and tests.

The assembler, bench, debugger, waveforms, VS Code highlighting and arch study then work for it unchanged.

## Contributing

1. Branch from `main`: `git switch -c <area>/<topic>`.
2. Make small commits. Keep `uv run pytest`, `uv run ruff check pioscope` and `uv run mypy` clean, and regenerate generated files (`pioscope isa-docs`, `pioscope arch --write`, `pytest --update-golden`) instead of hand-editing them.
3. Open a pull request. ISA changes need an ADR first ([isa-flow.md §4](docs/reference/isa-flow.md#4-checklists)). The working rules are in [CLAUDE.md](CLAUDE.md).

---

## Tiny Tapeout

The bench targets a [Tiny Tapeout](https://tinytapeout.com) tile: the DUT's pads are its `ui`/`uo`/`uio` pins. The project uses the Tiny Tapeout Verilog template:
- the description is in [docs/info.md](docs/info.md);
- the RTL goes in `src/`, with its sources listed in [info.yaml](info.yaml) (`source_files`, `top_module`);
- the cocotb testbench is in [test/](test/README.md).

The GitHub action builds the ASIC files with [LibreLane](https://www.zerotoasiccourse.com/terminology/librelane/).

- [Enabling GitHub Pages](https://tinytapeout.com/faq/#my-github-action-is-failing-on-the-pages-part) for the results page
- [Tiny Tapeout FAQ](https://tinytapeout.com/faq/) · [Digital design lessons](https://tinytapeout.com/digital_design/)

## License

Apache-2.0, see [LICENSE](LICENSE). The RP2040 PIO instruction set is Raspberry Pi Ltd's; this project models it from the public RP2040 datasheet and is not affiliated with Raspberry Pi.

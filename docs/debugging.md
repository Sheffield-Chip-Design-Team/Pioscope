# Debugging and waveforms: a guide

**Audience:** anyone who writes programs for an ISA pack or changes an engine model, and wants to see exactly what happens, cycle by cycle. You need Python and a terminal. You don't need to know the simulator. The examples use the RP2040 PIO pack; its model is described in [pio/machine-model.md](pio/machine-model.md).

**What you get:** three views of one deterministic run.

| Want | Use | Section |
|---|---|---|
| A timeline of what happened | `pioscope sim … --dut engine --trace` | [1](#1-trace-events) |
| Every register and pin on every cycle, as waveforms | `--vcd F` (GTKWave or Surfer) | [2](#2-waveforms-vcd) |
| To stop, step and inspect | `pioscope debug` (terminal) or VS Code (F5) | [3](#3-the-terminal-debugger-pioscope-debug), [4](#4-vs-code) |

All three run **the same simulation**. The debugger steps the same `PySession` that `pioscope sim` runs, and every run is deterministic: the same inputs always give the same cycles. A bug you see once, you can reach again with the same breakpoints.

**The tools follow the model.** Nothing in the trace, the VCD writer or the debugger names a register, a unit or a core. They read whatever the model reports (ADR-0010, §6 below). Add a core or a unit to a model and it appears in all three views, with no change to the tools.

---

## 0. Setup (once)

```bash
uv sync                                   # Python environment
uv run pioscope --help                      # the CLI
sudo apt install gtkwave                  # waveform viewer (or use Surfer)
uv run python tools/vscode-pioscope/build.py   # VS Code extension package
code --install-extension tools/vscode-pioscope/pioscope-asm-0.1.0.vsix
```

Then, in VS Code, run **Developer: Reload Window** (Ctrl+Shift+P). If VS Code can't find `uv`, set `pioscope.adapterCommand` to its full path (`~/.local/bin/uv`). Rebuild and reinstall the extension whenever `tools/vscode-pioscope/` changes.

**Vocabulary:**
- **Core:** one core of an engine model, with a PC, X/Y, shift registers, FIFOs and any units of its own. The PIO model calls its cores state machines and names them `sm0`, `sm1`, …
- **Unit:** hardware attached to a core, or shared between cores. The PIO model's GPIO outputs and IRQ flags are shared units.
- **sysclk cycle:** one step of the whole simulation (50 MHz by default).
- **ISA pack:** an instruction set with its assembler hooks, engine model and programs ([ADR-0012](open_items/0012-isa-packs.md)). A source file's suffix (`.pio`, …) picks its pack, and `--dut engine` builds that pack's model.

---

## 1. Trace events

```bash
uv run pioscope sim sim/packs/pio/programs/uart_rx.pio --dut engine --device uart_partner \
    --baud 115200 --uart-send 0x55 --trace --source sm0 --source uart_partner
uv run pioscope sim --scenario sim.packs.pio.tests.scenarios:uart_tx --dut engine --trace
```

`pioscope sim` and `pioscope debug` accept source (assembled on the fly) or image JSON. Each line shows the exact time, the cycle, the **source** and the event:

```
             time       cycle  source        event       data
        1.0800 us  @       54  sm0           exec        pc=0 op=wait 0 pin 0 src=sim/packs/pio/programs/uart_rx.pio:10
        1.0800 us  @       54  sm0           stall       pc=0 reason=wait 0 pin 0
       17.3611 us  @      868  uart_partner  sent        value=0x55 parity_error=False framing_error=False break_bits=0
       19.5200 us  @      976  sm0           exec        pc=1 op=set x, 7 [10] src=sim/packs/pio/programs/uart_rx.pio:11
```

The sources are:
- each core, by name (`sm0`, …);
- `shared`, for shared units;
- `host`, for FIFO push/pop;
- each device, by name (`uart_partner`, …).

Use `--source` (repeatable) to filter.

Every model emits these events:

| Event | Data | When |
|---|---|---|
| `exec` | `pc`, `op`, `src` | an instruction issues |
| `stall` | `pc`, `reason` | it starts waiting (`tx fifo empty`, `wait …`, `delay`, …) |
| `pull` / `push` | `word`; `auto` for autopull/autopush; `dropped` for a non-blocking push to a full FIFO | FIFO transfers |
| `irq` | `op`, `n` | an IRQ flag is raised |

A model adds its own events. In tests, the same events are in `Result.logs["sm0"]`, one list per source.

---

## 2. Waveforms (VCD)

`--vcd build/x.vcd` writes every bus net and every model probe, recorded whenever it changes. The hierarchy follows the model:

```
bench                 the pins/nets (tx, rx, sda, ...) and bench.clocks (device clocks)
engine
  sm0                 pc x y osr osr_count isr isr_count txf rxf flags irq stall out in
    units
      <any unit>      its probes
  sm1 ...             the same for every other core
  shared
    gpio              out oe (the PIO block's pins)
    irq               flags
```

For the PIO model, `out` and `in` are GPIO-numbered: ui0–7 = 0–7, uo0–7 = 8–15, uio0–7 = 16–23.

**Viewing:** next to `x.vcd`, the debugger also writes `x.gtkw`, a GTKWave save file with the signals already laid out:
- the bench nets first;
- then one group per core, with a sub-group per unit;
- then the shared units.

Multi-bit signals are shown in hex.

```bash
gtkwave build/x.vcd build/x.gtkw &
```

For a `pioscope sim` run you can make the save file yourself:

```python
from sim.bench.backends.python import run_python
from sim.dut.pio import PioEngine
from sim.packs.pio.tests.scenarios import uart_tx
r = run_python(uart_tx(payload=(0x41,)), PioEngine())
r.waveform.to_vcd("build/tx.vcd"); r.waveform.to_gtkw("build/tx.gtkw", "build/tx.vcd")
```

**GTKWave basics:**
- Ctrl+Shift+R reloads the file.
- Alt+F zooms to fit.
- To change hex/decimal/binary, click a signal, then right-click → Data Format.
- File → Write Save File keeps your own layout.

[Surfer](https://surfer-project.org) also opens the VCD (`surfer build/x.vcd`).

---

## 3. The terminal debugger: `pioscope debug`

```bash
uv run pioscope debug sim/packs/pio/programs/uart_rx.pio --device uart_partner --baud 115200 \
    --uart-send 0x55,0xA3
uv run pioscope debug --scenario sim.packs.pio.tests.scenarios:uart_rx --vcd build/rx.vcd
uv run pioscope debug sim/packs/pio/programs/uart_tx.pio --device uart_partner --baud 115200 \
    --host-tx 0x41 --dut mypackage.mymodel:MyEngine     # debug your own model
```

It takes the same program, scenario and device options as `pioscope sim`, plus:
- `--dut` chooses the model (default `engine`: the program's pack's model; or any `package.module:callable`);
- `--vcd FILE` keeps a waveform up to date (§5).

A short session:

```
(kdb) break bitloop
Breakpoint 1 at pc=2: file sim/packs/pio/programs/uart_rx.pio, line 13.
(kdb) continue
Breakpoint 1, bitloop () at sim/packs/pio/programs/uart_rx.pio:13
  13            in   pins, 1                 ; sample a data bit
(kdb) x/4i 0
   start:
      0  wait 0 pin 0                     ; uart_rx.pio:10
      1  set x, 7 [10]                    ; uart_rx.pio:11
   bitloop:
=>B   2  in pins, 1                       ; uart_rx.pio:13
      3  jmp x-- bitloop [6]              ; uart_rx.pio:14
(kdb) finish
[cycle 1627] pc=3 at sim/packs/pio/programs/uart_rx.pio:14
(kdb) p/x isr
$ = 0x80000000
(kdb) info shared
gpio: out=0x000000, oe=0x000000
irq: flags=0b00000000
(kdb) watch isr                  # stop when ISR changes
(kdb) catch event sm0:push
(kdb) display/x isr
(kdb) continue
```

| Command | Does |
|---|---|
| `run` / `start` | restart from cycle 0 and continue / stop at entry |
| `continue` (`c`) `[CYCLES]` | run to a breakpoint, watchpoint, catchpoint, the end, or a cycle limit |
| `step` (`s`, `next`, `n`) `[N]` | run N instructions of the selected core. A multi-cycle instruction runs to completion; it stops before the next instruction issues. |
| `stepi` (`si`) `[N]` | N sysclk cycles |
| `finish` | until the selected core's current instruction completes |
| `until` / `advance LOC` | continue to a location (temporary breakpoint) |
| `break` (`b`) `LOC`, `tbreak LOC` | LOC is a label, `*addr`, `line` or `file:line`, optionally prefixed by a core (`sm1:loop`). It stops **before** that instruction issues. Without a prefix it applies to every core running that code. |
| `delete [N]`, `disable N`, `enable N` | manage breakpoints, watchpoints and catchpoints |
| `watch EXPR` | stop when EXPR changes |
| `catch event SRC[:KIND]` | stop on an event: `uart_partner:rx`, `sm0:push`, `shared`, `host:pop` |
| `print` (`p`) `[/x /t /d] EXPR`, `display EXPR`, `undisplay` | values; `display` repeats at every stop |
| `core [NAME\|N]` (`thread`) | select the core that step, finish, print and info act on |
| `info cores` | every core: PC, instruction, location, stall reason (`*` = selected) |
| `info registers / flags / fifos / units [NAME] / pins / shared / events [N] / breakpoints / program / scenario` | the state, recent events, the disassembly. `info KEY` works for any key of the core's state (`info config` on the PIO model). |
| `x/Ni [LOC]` | disassemble N instructions (`=>` marks the PC, `B` a breakpoint) |
| `list [LOC]` | source around the PC |
| `bt` | the frame: core, pc, label, instruction, line, cycle |
| `set var x = 0x10` | change x, y, osr, isr or pc (pc only between instructions); `set var sm1.x = 3` |
| `set trace on` | print every event while running |
| `vcd FILE` / `vcd off` | write the waveform so far to FILE (and FILE.gtkw) at every stop |
| empty line | repeat the last command |

**Expressions** (`print`, `watch`, `display`, hover in VS Code) are resolved in this order:
1. `$cycle`, `$time`;
2. a path from the top: `cycle`, `shared.<unit>.<field>`, `cores.sm1.regs.x`;
3. a path from a core by name: `sm1.regs.x`, `sm1.x`;
4. a path from the selected core: `regs.x`, `fifo.tx.level`, `pins.in.rx`, `config.out_shift`;
5. a bare name that occurs once in the selected core's state: `x`, `isr`, `pc`. An ambiguous name lists the full paths to choose from.

**Scripting:** `-ex CMD` (repeatable) and `--commands FILE` run commands at start, and `--batch` exits after them. The tests use this (`sim/packs/pio/tests/test_debug.py`):

```bash
uv run pioscope debug sim/packs/pio/programs/uart_tx.pio --device uart_partner --baud 115200 \
    --host-tx 0x41 --batch -ex "break 13" -ex c -ex "p/x osr"
```

---

## 4. VS Code

The extension in `tools/vscode-pioscope/` adds syntax highlighting for every pack's source files and a `pioscope` debugger. Its adapter is `pioscope dap`: the same debugger, spoken over the Debug Adapter Protocol.

1. **Start.** Open a program (`.pio`, …), click in the gutter to set breakpoints, and press F5.
   - With no `launch.json`, F5 debugs the active file with a UART partner attached.
   - `.vscode/launch.json` has ready-made configurations.
2. **Toolbar:**

   | Button | Runs |
   |---|---|
   | Continue | to the next breakpoint |
   | Step Over | one instruction of the selected core |
   | Step Into | one sysclk cycle |
   | Step Out | until the current instruction finishes |
   | Restart | back to cycle 0 (deterministic: the same breakpoints reach the same state) |

3. **Inspect.** Everything is in the **Run and Debug** sidebar (Ctrl+Shift+D):
   - **Call Stack:** one thread per core (`sm0`, `sm1`, …). Click one to inspect it; stepping applies to the thread you select.
   - **Variables**, for the selected core:
     - *Core sm0* (pc, label, instruction, stall reason, …);
     - one scope per group of its state: Registers, Flags, FIFOs, Units, Pins, Config, and any group a model adds;
     - *Shared* when the model has shared units;
     - *Timing* (cycle, µs).

     Edit x/y/osr/isr/pc in place. Right-click → **Break on Value Change** sets a watchpoint.
   - **Watch:** add any expression from §3.
   - **Hover** over a name in the editor to see its value.
   - **Disassembly:** right-click in the editor → **Open Disassembly View**. It shows the instruction memory with the binary words (16-bit on PIO) and the labels. Click its gutter for an address breakpoint. An address `0xTAAA` means core T, word address AAA.
   - **Debug Console:** type kdb commands (`info cores`, `info shared`, `p/x isr`, `x/8i`, `catch event uart_partner:rx`, `info events 20`) or an expression. Running and stepping go through the toolbar.

   Panels named **Peripherals**, **Registers** or **Memory** come from other extensions (PlatformIO, Cortex-Debug) and stay empty here; hide them with a right-click on their header.

**Launch attributes** (`.vscode/launch.json`):

| Attribute | Meaning |
|---|---|
| `program` | the source file or image JSON to run |
| `scenario` | instead of `program`: a scenario factory, `module:function` |
| `dut` | the model: `engine` (default: the program's pack's model) or `package.module:callable` |
| `devices` | device presets: `uart_partner`, `spi_slave`, `i2c_slave` |
| `hostTx`, `uartSend` | words the host pushes; bytes the UART partner sends |
| `baud`, `ppm`, `cycles` | as in `pioscope sim`. `baud` is needed when the program does not state its rate (any PIO program). |
| `stopOnEntry` | stop before the first instruction |
| `trace` | print every event to the Debug Console |
| `vcd` | a VCD (and `.gtkw`) rewritten at every stop (§5) |
| `cwd` | the project directory (default: found from the program's path) |

---

## 5. Watching waveforms while you step

Set `"vcd": "${workspaceFolder}/build/debug.vcd"` in the launch configuration, or type `vcd build/debug.vcd` in the Debug Console or kdb. At every stop the debugger rewrites:
- `build/debug.vcd`: everything recorded so far, cut at the current cycle;
- `build/debug.gtkw`: the layout.

1. Start debugging. The files appear at the first stop.
2. Open them once: `gtkwave build/debug.vcd build/debug.gtkw &`.
3. Step in VS Code (or kdb), then press **Ctrl+Shift+R** in GTKWave to reload. The trace grows to the cycle you stopped at, and your layout is kept.
4. Restart cuts the file back to cycle 0.

The viewer does not follow the debugger on its own: reload it after each step.

---

## 6. How the tools follow the model

The debugger, the trace and the VCD need only the **observability contract** (ADR-0010, [reference](reference/simulator.md)):

| The model provides | The tools show |
|---|---|
| `dut.cores`: each with a `name`, a program, `state()`, `probes()` | one debugger thread per core; one VCD scope `engine.<name>` |
| `core.state()`: scalar keys (pc, label, instr, stall, …) plus dict-valued groups | the *Core* scope, plus one Variables scope per group; `info KEY` |
| `core.units`: a list of `Unit` objects with `name`, `state()`, `probes()` | the Units scope, `info units`, `engine.<t>.units.<name>.*` in the VCD |
| `dut.shared`: a list of shared `Unit`s | the Shared scope, `info shared`, `engine.shared.<name>.*` |
| events as (source, event) | trace lines, `catch event SOURCE:KIND`, `Result.logs[source]` |

So:
- **To add a register or a field:** put it in the core's `state()` (and in `probes()` if it is an integer you want in the VCD).
- **To add a unit:** subclass `sim.dut.core.Unit` and append an instance to `core.units`.
- **To add a core or a shared unit:** subclass the engine and override `build()` to fill `self.cores` and `self.shared`. Then debug it with `--dut package.module:MyEngine` (or `"dut"` in `launch.json`).

`sim/packs/pio/tests/test_debug.py` proves this with a two-SM model that exists only in the test.

---

## 7. Recipes

**Watch a byte arrive bit by bit (UART RX on PIO).**
1. `break bitloop`, then `continue`.
2. `display/x isr` and `display isr_count`.
3. `continue` repeatedly. Each stop is one sampled bit, shifted in from the top (`in_shift` right).
4. In GTKWave, put the `rx` net next to `engine.sm0.isr`: every sample is 8 SM cycles (one bit) after the last.

**Measure a bit period.** In GTKWave, click the start edge of a bit on the bus net, then the next edge (middle-click sets a second marker). The delta is shown at the top. 115200 baud is 8.68 µs.

**See side-set and delays (SPI on PIO).** Put `sck`, `mosi` and `engine.sm0.pc` together. Each edge of SCK lines up with the issue of the instruction that side-sets it. The `[1]` delays show up as PC holding for one extra SM cycle.

**Stop when the host reads a word.** `catch event host:pop`, then `info fifos`.

**Compare two cores.** `info cores` lists every PC. `print sm0.isr` and `print sm1.isr` side by side. `watch sm1.regs.x` stops when sm1 changes X, whichever core is selected.

**See contention** (once a model has shared units with arbiters). Put the arbiter's `grant` and `request` probes from `engine.shared.<arb>` next to each core's `stall` probe in GTKWave. Then `catch event shared` stops at every arbitration event the unit emits.

---

## 8. Using this for more than debugging

The debugger and the waveforms are also the instruments for architecture work:

- **Measure real activity.** The architecture study (`sim/arch`, `docs/arch-guide.md`) estimates activity from a `load` number. A unit's probes (busy, bits fed) give measured busy cycles per protocol and per bit; feed those back into the pack's `workloads.yaml`.
- **Contention under load.** Model a pooled unit as a shared `Unit` with an arbiter. Run several protocol programs on several cores and plot `grant`/`stall`. Count stalled cycles and missed bit deadlines as the load rises; the trace gives exact times.
- **Runtime reconfiguration.** Model the controller as a shared unit that swaps programs or unit configurations. Step through a swap in the debugger and check in the waveform that no bit on the bus was disturbed.
- **Model vs RTL.** The same scenario runs on the Python model (`pioscope sim`) and on RTL through cocotb (`pioscope rtl --vcd`). Diff the two VCDs' bus nets: any difference is a bug in one of them.
- **Regression by waveform.** `Result.waveform.digest()` is a hash of the whole run. A test can pin it to catch any change in timing, even one cycle.

No reverse stepping: restart instead, since runs are deterministic.


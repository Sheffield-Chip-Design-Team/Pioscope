# Simulator reference

**Audience:** engineers changing or extending the simulator: a new unit, more cores, a new engine model, a new ISA pack, a new device. It assumes Python and basic digital design (registers, clocks, FIFOs). The user guide for running and debugging is [debugging.md](../debugging.md), and the bench user guide is [bench.md](../bench.md).

**Contents:**
1. The big picture
2. One sysclk cycle
3. The DUT contract
4. The engine core
5. How an instruction executes
6. Units
7. The observability contract
8. Rules every model follows
9. How to …
10. Where the tests are

---

## 1. The big picture

```
 source ──piolab asm──▶ ProgramImage ──load──▶ DUT (an ISA pack's engine model, or RTL)
  (pack by suffix)                              ▲   │ PadOut (uo, uio + OE)
                                  PadIn (ui,uio)│   ▼
                     Board: nets, pulls, wired-AND, contention checks, waveform
                          ▲ drives          │ levels
                     Devices (UART partner, SPI slave, I2C slave/master, ...)
                                            │
                 Result: waveform, device logs, host log, engine trace ─▶ decoders, checks, tests
```

| Layer | Code | Owns |
|---|---|---|
| ISA packs | `piolab/packs/`, `<pack>/isa.yaml` | one instruction set each: spec, hooks, model, programs, arch data ([ADR-0012](../open_items/0012-isa-packs.md)) |
| ISA | `piolab/isa/` | loads a pack's yaml: instructions, operands, directives, encoding ([isa-flow.md](isa-flow.md)) |
| Assembler | `piolab/asm/` | source → `ProgramImage`; the generic field encoder and the encoder hook |
| Image | `piolab/image.py` | IR (`IrInstr`: addr, name, operands, side, delay, src, text), words, config, symbols, the pack name |
| Bench | `piolab/bench/` | board, nets, pads, wiring, scenarios, the cycle loop (`backends/python.py`), cocotb backend |
| DUT slot | `piolab/dut/` | the contract (`base.py`), stand-ins (`stub.py`), the engine core (`core/`), the pack models (`pio/`, …) |
| Devices | `piolab/devices/` | the other end of each bus |
| Decoders | `piolab/decoders/` | independent waveform decoders; they may import only `piolab/wave.py` |
| Debugger | `piolab/debug/` | session (core), repl (terminal), dap (VS Code) ([debugger.md](debugger.md)) |
| Arch study | `piolab/arch/` | demand, allocation, area model, report ([arch-guide.md](../arch-guide.md)) |

---

## 2. One sysclk cycle

`PySession.step()` (`piolab/bench/backends/python.py`) runs cycle k. The phase order is ADR-0002:

1. The host driver offers TX words (`host_push`) before the edge.
2. `Board.cycle(k, out)`:
   - resolves the bus from the DUT's registered outputs and the devices' previous drives;
   - steps every device on that view;
   - resolves the settled bus (contention is checked here);
   - records the waveform;
   - returns the `PadIn` the DUT samples.
3. `dut.step(pins_in)`: one rising edge. It returns the outputs registered at that edge, which the bus shows in cycle k+1.
4. The host driver pops RX words (`host_pop`).
5. The DUT's trace events (`drain_events()`) and probes (`probes()`) are collected.
6. The run stops at `max_cycles` or when the scenario's `until` returns true.

Inside an engine built on the core, one `step` is:
1. pads → the 2-flop synchroniser (`core/sync.py`);
2. each core's `sysclk()`:
   - `before_exec()`, the model's per-cycle work (none on PIO);
   - on a clock-divider enable, the core continues or issues an instruction;
   - each unit's `on_cycle()`;
3. each shared unit's `on_cycle(engine)`;
4. outputs → pads (`Engine._pads`, which a model overrides for its own pin model).

Timing consequences:
- An input change is seen 2 cycles after it reaches the pad.
- An output change appears on the bus 1 cycle after the instruction that made it.

---

## 3. The DUT contract

`piolab/dut/base.py` is a `Protocol`. Anything that implements it can sit in the DUT slot (`--dut package.module:callable`). `--dut engine` (the default of `piolab sim` and `debug`) builds an engine model of the program's ISA pack: `--model NAME`, else `[tool.piolab] model`, else the pack's `default_model`. The test default `--dut auto` does the same for scenarios that need an engine.

| Method | Meaning |
|---|---|
| `is_engine: bool` | False for stand-ins (engine scenarios then skip) |
| `load(image)` | take a `ProgramImage` (backdoor load: no instruction memory writes are simulated) |
| `reset() -> PadOut` | outputs while in reset / just released |
| `step(PadIn) -> PadOut` | one rising edge |
| `host_push(tc, word) -> bool` | offer a word to core `tc`'s TX FIFO; False if full |
| `host_pop(tc) -> int \| None` | take a word from core `tc`'s RX FIFO |
| `host_irq() -> int` | raised IRQ flags |
| `probes() -> {name: int}` | optional: integer signals for the VCD |

The observability members (§7) are optional too, but a model without them can't be debugged.

---

## 4. The engine core (`piolab/dut/core/`)

Every pack's model builds on the same core:

| File | Contents |
|---|---|
| `engine.py` | `Engine`: the DUT. `build(image)` (each model overrides it) fills `self.cores` and `self.shared`. `step`, the host port, `_pads`, and the aggregation of state/probes/events. |
| `core.py` | `Core`: architectural state (pc, x, y, osr/isr + counts, `FLAGS`, irq, FIFOs), its `units` list, pins (`out_level`, `out_oe`, `pads_seen`), execution state (`cur`, `op`, `delay_left`, `stall`, `div_acc`), handler dispatch, side-set, delay, wrap, `state()`, `probes()`, `issue_pc()`, `set_reg()` and the hooks below. `registry()` makes a handler table. |
| `config.py` | `CoreConfig.from_image`: roles (`.pins`), FIFO depths, wrap, side-set, clkdiv. A model subclasses it for its own directives. |
| `unit.py` | `Unit`, the interface every unit implements |
| `sync.py`, `fifo.py` | input synchroniser; bounded FIFO (depth 0 allowed) |
| `errors.py` | `EngineError` (a program did something invalid), `EngineUnsupported` (the model doesn't model it) |

**Hooks** a model overrides on `Core`:

| Hook | Default | PIO (`piolab/packs/pio/models/base/`) |
|---|---|---|
| `before_exec()` | nothing | nothing |
| `fetch()` | `prog[pc]` | an `out exec` / `mov exec` word first |
| `side_set(ins)` | drive the side-set roles at issue | deferred to `post_handler`, so side-set wins over a same-cycle write |
| `post_handler(ins, first)` | nothing | applies the side-set |
| `retire(ins)` | advance the PC (unless it jumped), start the delay | an exec'd instruction leaves the PC alone |
| `pins_state()`, `in_bits()`, `out_bits()` | per `.pins` role | GPIO-numbered (the shared `gpio` unit) |

**Pins (core default):**
- Roles are numbered in `.pins` order, inputs and outputs separately.
- `in_level(n)` reads input role n (synchronised).
- `drive_out(n, level)` drives output role n, and handles open-drain and `z`.

A model can replace this. The PIO model numbers pins as GPIOs (ui0–7 = 0–7, uo0–7 = 8–15, uio0–7 = 16–23) and keeps their levels in a shared unit.

**Clock divider:** `.clkdiv` gives a fractional enable. `_enabled()` adds 256 each sysclk and fires on reaching `clkdiv` (units of 1/256 cycle), as on PIO.

---

## 5. How an instruction executes

A handler has the signature `handler(tc, ins: IrInstr, first: bool) -> bool`.

- It is called on every core cycle while `ins` is current. `first` is True on the issue cycle.
- It returns **True when the instruction completes**, False to stall. On a stall the PC holds, and `tc.op["stall"]` names the reason shown in the trace and the debugger.
- `tc.op` is a scratch dict for multi-cycle instructions. It is cleared at issue.
- To jump, a handler sets the PC and `tc.jumped`. Otherwise `retire` advances the PC, wrapping at `.wrap`.
- Side-set is applied by the core (`side_set`), not by the handler. `[delay]` cycles are added after completion.
- Trace events come from `tc.emit(kind, **data)`. Flags are set with `tc.set_flag(name)`.

Registration (from `piolab/packs/pio/models/base/exec.py`):

```python
HANDLERS, handler = registry()

@handler("jmp")
def _jmp(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    if _cond(sm, ins.operands["cond"]):
        sm.jump(int(ins.operands["target"]))
    return True
```

Each pack's tests fail if a handler is missing or extra (`piolab/packs/pio/tests/test_exec.py::test_every_instruction_has_a_handler`).

---

## 6. Units

Every unit subclasses `Unit` (`piolab/dut/core/unit.py`):

```python
class Unit:
    name: ClassVar[str]                      # unique in its core / in Engine.shared
    def state(self) -> dict[str, Any]: ...    # shown in Variables > Units, `info units`
    def probes(self) -> dict[str, int]: ...   # recorded into the VCD on change
    def probe_bits(self) -> dict[str, int]: ...  # widths (default 32)
    def on_cycle(self, owner) -> None: ...    # once per sysclk, after the owner's work
```

A core can keep typed attributes for the units its instructions call (`tc.crc`, …) **and** the list `tc.units`, which the tools iterate over. A unit that only observes (a counter, a monitor) needs to be in the list only. The PIO model has no per-SM units. Its block-level state is two shared units:

| Unit | Driven by | State | Probes |
|---|---|---|---|
| `gpio` | `out`/`set`/`mov` pins and pindirs, side-set, `.sm_config high=` | `out`, `oe` (24 bits) | `out`, `oe` |
| `irq` | `irq`, `wait irq` | the 8 flags | `flags` |

---

## 7. The observability contract (ADR-0010)

The trace, the VCD writer and the debugger never name a register, unit or core. They use only:

| Member | Shape |
|---|---|
| `dut.cores` | a list; each has `name`, `prog` (list of `IrInstr`), `labels` ({addr: label}), `image`, `issued`, `cur`, `pc`, `issue_pc()`, `set_reg(name, value)`, `state()`, `probes()`, `probe_bits()` |
| `dut.state()` | `{"cycle", "time_s", "cores": {name: core.state()}, "shared": {unit: unit.state()}}` |
| `core.state()` | the scalars `name pc label instr src issued in_progress stall delay_left irq` plus any dict-valued groups (`regs flags fifo units pins` from the core, `config` on PIO) |
| `dut.probes()` / `probe_bits()` | `{"sm0.pc": …, "sm0.units.<unit>.<probe>": …, "shared.<unit>.<probe>": …}` |
| `dut.drain_events()` | `[(source, Txn)]`, the source being a core name or `shared`; shared units call `engine.emit_shared(kind, **data)` |

What each becomes:
- Each core becomes a debugger thread and a VCD scope `engine.<name>`.
- Each dict-valued group becomes a Variables scope and an `info` subject.
- Each unit becomes an entry under Units and a VCD scope `engine.<t>.units.<unit>`.
- Each shared unit appears under Shared and as `engine.shared.<unit>`.
- Each event source can be used in `catch event SOURCE:KIND` and `Result.logs[SOURCE]`.

`piolab/packs/pio/tests/test_debug.py` checks all of this with a two-SM model defined only in the test.

---

## 8. Rules every model follows

From CLAUDE.md §6:
- **Deterministic.** No wall-clock time, no unseeded randomness, and no iteration over unordered sets that affects behaviour. `Result.waveform.digest()` must be identical for identical inputs.
- **Bounded work per cycle.** Everything a model does in one `step` should be something hardware can do in one cycle. Keep loops bounded by fixed sizes (pins, FIFO depth), never by data.
- **Explicit widths.** Mask registers (`& M32`), and give every probe a width.
- **ISA semantics come from the pack's yaml.** Where the spec leaves a choice open, record the choice in an ADR. If a protocol needs something the ISA lacks, that is a finding: record it first (agreements #2, #3).
- **Decoders stay independent.** Never import an engine or the devices into `piolab/decoders/`.

---

## 9. How to …

**…add a field to a core's state, or a register to the VCD:**
1. Add the attribute in your core's `__init__`.
2. Add it to `state()`: any group, or a new dict-valued group, which becomes a new VS Code scope.
3. For the VCD, add it to `probes()` and its width to `PROBE_BITS`.

No debugger change is needed.

**…add a unit:**
1. Write `class MyUnit(Unit): name = "myunit"`.
2. Implement `state()` / `probes()` / `probe_bits()`, plus `on_cycle()` if it acts every cycle.
3. Create it in the core (or the engine's `build`) and append it to `units` (or `shared`). Keep it as an attribute too if handlers will call it.
4. Write directed tests for it. [Tutorial 04](../tutorials/04-add-a-unit.md) walks through one.

**…add a core, or shared units (a new architecture model):**

Subclass the pack's engine and override `build`:

```python
class TwoSms(PioEngine):
    def build(self, image):
        super().build(image)                     # sm0, and the shared gpio/irq units
        cfg = PioConfig.from_image(image)
        self.cores.append(PioSm(image, cfg, HANDLERS, self.fs, "sm1", self, 1))
        self.shared.append(MyArbiter())          # a Unit; on_cycle(engine) sees all cores
```

Put it in the pack as `models/two_sms/` (exporting `Engine = TwoSms` and `HANDLERS`) and run it with `piolab sim … -m two_sms`, `piolab debug … -m two_sms`, `pytest --model two_sms` or `"model"` in `launch.json`. Outside a pack: `--dut mypkg.mod:TwoSms`.

The image format holds one program, so every core runs the same one. Giving each core its own program, pins and host port is future work.

**…add an engine model for a new ISA:** see [isa-flow.md](isa-flow.md) §5. Subclass `piolab.dut.core.Core` and `Engine`, write one handler per instruction with `registry()`, and name the model in the pack's `PACK`.

**…add a trace event:** call `tc.emit("kind", key=value)` in the core or a handler, or `engine.emit_shared(...)` in a shared unit. Document it in [debugging.md](../debugging.md) §1.

**…add an instruction:** see [isa-flow.md](isa-flow.md). It starts in the pack's yaml, and it is an ISA change (ADR first).

**…add a device or a decoder:** see [bench.md](../bench.md), "Writing a device", and the decoder rules there.

---

## 10. Where the tests are

| Area | Tests |
|---|---|
| A pack's handlers (directed, one behaviour each) | `piolab/packs/pio/tests/test_exec.py` |
| A pack's encoding | `piolab/packs/pio/tests/test_encode.py` |
| A pack's programs against devices and decoders | `piolab/packs/pio/tests/test_scenarios.py` (run on the model by default, `--dut auto`) |
| Debugger (REPL and DAP), tools follow the model | `piolab/packs/pio/tests/test_debug.py` |
| Bench, devices, decoders, examples, VS Code extension | `piolab/tests/` |

Run `uv run pytest`, `uv run ruff check piolab` and `uv run mypy` before every commit.


# ADR-0010: Cores, and an observability contract the tools follow

Status: accepted (2026-11-11).

## Context
The next phase models architectures with several cores, shared or pooled units, a reconfiguration controller, and contention under load. Several engineers will build those models in parallel, by hand.

The tools were hard-coded to the first model:
- the debugger had one thread and a fixed list of scopes, read one core by attribute and aliased six register names;
- the VCD wrote every probe flat under `engine`.

Each new core or unit would have meant editing the debugger, the bench and the VCD writer. That is slow, and it causes merge conflicts between work packages.

## Decision
1. **Observability contract.** A model is debuggable and traceable when it provides:

   | Member | Shape | Used by |
   |---|---|---|
   | `cores` | list of cores, each with `name`, `prog`, `labels`, `image`, `issued`, `cur`, `issue_pc()`, `set_reg()`, `state()`, `probes()`, `probe_bits()` | debugger threads, breakpoints, stepping, disassembly |
   | `state()` | `{"cycle", "time_s", "cores": {name: core.state()}, "shared": {unit: state}}` | Variables, `print`, `watch`, `info` |
   | `core.state()` | scalars `name, pc, label, instr, src, issued, in_progress, stall, delay_left, irq`, plus any dict-valued groups (`regs`, `flags`, `fifo`, `units`, `pins`, …) | one VS Code scope per group, the scalars in a "Core" scope |
   | `probes()` / `probe_bits()` | `{"sm0.pc": 3, "sm0.units.<unit>.<probe>": 17, "shared.arb.grant": 1}` | VCD scopes `engine.sm0`, `engine.sm0.units.<unit>`, `engine.shared.arb`; GTKWave save-file groups |
   | `drain_events()` | `[(source, Txn)]`, source = core name or `shared` | trace, `catch event`, `Result.logs[source]` |

   - Units subclass `sim.dut.core.Unit` (`name`, `state()`, `probes()`, `probe_bits()`, `on_cycle()`).
   - A core iterates over its `units` list, and the engine over its `cores` and `shared` lists. Adding a unit, a core or a shared unit needs no change to the tools.
   - Bare events from older DUTs are logged under the source `engine`.
   - The generic core (`sim.dut.core`, ADR-0012) implements all of this, so a model built on it gets the contract for free.
2. **The debugger takes the model as a parameter:** `pioscope debug --dut module:callable`, or `"dut"` in `launch.json`.
   - Threads are cores. Scopes come from the core's state.
   - Breakpoints apply to every core running that code, or to one, with a `<name>:` prefix.
   - `step`/`finish` act on the selected core; the others run in the same cycles.
   - Disassembly addresses carry the core: `0xTAAA`.
3. **Waveform layout follows the model.** Probe names are nested scopes in the VCD, and `Waveform.to_gtkw()` writes a GTKWave save file grouped per core, unit and shared unit. The debugger writes both at every stop when `vcd` is set.

## Consequences
- `sim/packs/pio/tests/test_debug.py` keeps the promise. A two-SM model with an extra unit, defined only in the test, is traced, recorded, and debugged in kdb and over DAP. The PIO model's shared `gpio` and `irq` units appear without tool changes.
- New models subclass the core `Engine` and override `build()`, or provide the contract directly.

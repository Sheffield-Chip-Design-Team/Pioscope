# ADR-0004: Abstract loader, backdoor first

Status: accepted (2026-11-10)

## Decision
- `sim.bench.loader.Loader` has two implementations:
  - `PyImageLoader` hands the `ProgramImage` object to Python DUTs.
  - `MemFileLoader` writes `prog.hex` (one word per imem slot, for `$readmemh`) and `cfg.json`, and passes `+PROG=` and `+CFG=` plusargs to the simulator.
- `test/tb.v` reads `+PROG` into `` `PIOSCOPE_IMEM_PATH `` when that macro is defined, which will happen once the engine RTL exposes its instruction memory.
- `cfg` stays JSON until the engine has a configuration register map.
- A frontdoor loader over TT pins can be added later without touching scenarios.

## Open
The engine host port (TX/RX FIFOs, IRQs) has no RTL mapping. Python DUTs implement `host_push`/`host_pop`/`host_irq`. The RTL backend has an `RtlHost` seam, which by default refuses pushes.

# ADR-0001: Backend-neutral bench; cocotb for RTL only

Status: accepted (2026-11-10)

## Context
The phase goal is evidence that the ISA suits serial protocols, gathered before RTL exists, and the same tests must then run against the RTL. cocotb is the natural RTL harness, since the Tiny Tapeout template already uses it. But every Python step under cocotb goes through VPI, so it is slow. At 50 MHz, one UART byte is about 4,300 cycles, and property-based runs need many frames. ISA claims are timing claims (bit period, sample point, cycles per bit), so the model must be cycle-accurate rather than transaction-level.

## Decision
- Device models, decoders, checks and scenarios are written once, against the TT pin contract (`sim.dut.base.Dut`, `sim.bench.pads`).
- Devices are synchronous step machines, not cocotb coroutines.
- Two backends share one `Board` (`sim/bench/board.py`):
  - **python**: a pure-Python cycle loop with a Python DUT (the stub now, the engine model later).
  - **rtl**: a cocotb adapter around `test/tb.v` (`sim/bench/backends/cocotb_rtl.py`).
- Checks consume only a `Result` (waveform plus device logs), so they cannot depend on the backend.
- The drop-in guarantee is tested. The same scenario on `TtTemplateDut` and on `src/project.v` must give identical waveforms (`sim/tests/bench/test_rtl_parity.py`, and `test/test.py` in the TT `make` flow).

## Consequences
- Fast default runs (about 2 s for the full suite) on the Python backend.
- Any future DUT, model or RTL, must pass the parity test to show it plugs in.
- Device code must not use cocotb triggers. Anything async belongs in the adapter.

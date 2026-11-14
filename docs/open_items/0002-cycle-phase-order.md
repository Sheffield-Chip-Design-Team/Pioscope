# ADR-0002: Per-cycle phase order on the board

Status: accepted (2026-11-10)

## Decision
For sysclk cycle k, given the DUT outputs O_k registered at the previous rising edge:

1. `pre = resolve(O_k, D_{k-1})`. This is the bus the devices observe.
2. Every device steps on `pre` and returns its drives D_k.
3. `L_k = resolve(O_k, D_k)` gives the settled bus. Contention is checked here only.
4. `L_k` is recorded in the waveform at cycle k.
5. `L_k` is presented on `ui_in`/`uio_in`, and the DUT samples it at the next rising edge, producing O_{k+1}.

In cocotb, steps 1–5 run at the falling edge of `clk`. The Python backend mirrors this through `Dut.reset()` (gives O_0) and `Dut.step(L_k)` (gives O_{k+1}).

## Consequences
- A device sees the bus one cycle after anyone drives it, including itself. That latency is what a real synchronous model has, and it gives wired-AND readback for I²C arbitration and clock stretching.
- The DUT sees a device drive in the same cycle the device makes it, at the next rising edge.
- The engine's own input synchroniser (CLAUDE.md §3) is internal to the DUT and adds on top of this.
- Transient conflicts in `pre` are not errors. Only settled contention is.

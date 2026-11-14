# ADR-0003: Tiny Tapeout pad model and net resolution

Status: accepted (2026-11-10)

## Decision
- The DUT pin contract is the TT top level: `ui[7:0]` input, `uo[7:0]` output (always driven), and `uio[7:0]` bidirectional with `uio_oe`. Defaults are sysclk 50 MHz and imem 32 slots, and both are parameters.
- Board nets are named. Each optionally sits on one DUT pad and optionally has a pull.
- Resolution: an X drive gives X. 0 and 1 together give contention (`BusConflict`, raised by default, or recorded). Otherwise the driven level wins, and an undriven net takes its pull, or X if it has none.
- Open-drain is emulated. The DUT drives low with `uio_oe=1, uio_out=0` and releases with `uio_oe=0`. Devices on open-drain buses only drive 0 or Z. Wired-AND falls out of the rule above.
- The assembler rejects open-drain on pads without an OE (`ui`, `uo`). `Wiring.check_against(image)` reports a board that disagrees with the program's `.pins`, including a missing pull-up.
- X on a DUT input is passed to the RTL as X, and to Python DUTs as the `PadIn.*_x` masks.

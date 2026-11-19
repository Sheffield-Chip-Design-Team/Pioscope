# Tutorial 03: make a new value visible in the debugger and the waveform

**You will:** add a value to the core and see it in VS Code, the REPL and GTKWave without touching the debugger. **Background:** [reference/simulator.md §7](../reference/simulator.md#7-the-observability-contract-adr-0010).

The tools show whatever the model reports:
- `Core.state()` feeds the debugger: each dict-valued key becomes a VS Code scope.
- `Core.probes()` feeds the VCD.

## Do it: count instructions per core in the VCD

`Core.issued` already counts issued instructions, and it is in `state()`, so the debugger shows it under *Core sm0*. It is not in the VCD. Add it:

1. In `piolab/dut/core/core.py` (the core every model builds on), in `probes()`, add `"issued": self.issued & 0xFFFF,` to the dict.
2. In the class's `PROBE_BITS`, add `"issued": 16`.
3. Run:
   ```bash
   uv run piolab sim --scenario piolab.examples.pulse_counter:blink_scenario --vcd build/b.vcd
   gtkwave build/b.vcd &
   ```
   `engine.sm0.issued` is there. Right-click it → Data Format → Analog → Step to see it as a staircase.

## Do it: a new group in Variables

Add a dict-valued key to `Core.state()`:

```python
"perf": {"issued": self.issued, "cycles": self.cycle,
         "ipc_x1000": 1000 * self.issued // max(self.cycle, 1)},
```

Start a debug session: a **Perf** scope appears in VS Code. `info perf` and `print perf.ipc_x1000` work in `piolab debug`. No debugger file changed.

## Rules
- State values must be plain data: int, bool, str, None, and dicts or lists of those.
- Probes are integers with a width; mask them to that width.
- Keep `state()` cheap: it runs at every stop and on every hover.
- Revert these edits, or keep them with a test and a line in [debugging.md](../debugging.md).

## Exercise
Add a `stall_cycles` counter (incremented in `_exec` whenever `self.stall` is set). Show it in Perf and in the VCD. Use it to measure what fraction of time the PIO `uart_tx` waits for the TX FIFO.

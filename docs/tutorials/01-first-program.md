# Tutorial 01: your first program, from source to waveform

**You will:** assemble a program, run it, read its trace, look at its waveform, and step through it. **Time:** 20 minutes. **Needs:** setup from [debugging.md §0](../debugging.md#0-setup-once).

## 1. Read the program

[`piolab/examples/blink.pio`](../../piolab/examples/blink.pio), an RP2040 PIO program (the `.pio` suffix picks the PIO pack):

```asm
.program blink
.pins     led=uo0          ; the role "led" is output pad uo0
.clkdiv   25               ; the state machine runs at 50 MHz / 25 = 2 MHz
.wrap_target
          set  pins, 1  [7]    ; led high, then wait 7 more cycles
          set  pins, 0  [7]    ; led low
.wrap                          ; loop back for free
```

- **Directives** (`.program`, `.pins`, `.clkdiv`, `.wrap*`) become configuration. They take no instruction memory and never "run". `.pins` and `.clkdiv` are *harness* directives: on a real RP2040 the Pico SDK sets them from C.
- `set pins, 1 [7]` takes 1 cycle plus 7 delay cycles, so 8 state-machine cycles. At 2 MHz that is 4 µs.
- `.wrap` jumps back to `.wrap_target` without spending an instruction (as on PIO).

Predict before you run: high for 8 × 25 = **200 sysclk cycles**, then low for 200, so the period is 400 cycles = 8 µs.

## 2. Assemble it

```bash
uv run piolab asm piolab/examples/blink.pio --listing      # build/blink.json + build/blink.lst
cat build/blink.lst
```

`piolab asm` takes a path, or the name of a program in the active pack (`uv run piolab asm uart_tx` assembles `piolab/packs/pio/programs/uart_tx.pio`). Its outputs go to `build/` unless you give `-o` or `--listing FILE`.

The listing shows 2 instruction words, their 16-bit RP2040 encoding (`e701`, `e700`: the same words pioasm produces), and the configuration produced from the directives. Try an error: change `set` to `sett`, assemble again, and read the message (file:line:column, what was expected, and a hint).

## 3. Run it with a trace and a waveform

```bash
uv run piolab sim --scenario piolab.examples.pulse_counter:blink_scenario \
    --trace --vcd build/blink.vcd
```

- `--scenario` picks a ready-made test bench: the program, a pulse-counting device on the `led` net, and a stop condition. Tutorial 02 shows how it's written.
- `piolab sim` runs the program's ISA pack's engine model, here the PIO model (`-m NAME` picks another of the pack's models; `--dut stub` runs a stand-in that does nothing).
- Each trace line has the time, the cycle, the source (`sm0` = the state machine, `counter` = the device) and the event. You should see `exec` events and, every 400 cycles, `pulse … width=200`.

Open the waveform:

```bash
gtkwave build/blink.vcd &
```

In the left tree, open `bench` and add `led`, then open `engine` → `sm0` and add `pc` and `out` (GPIO-numbered: `led` on uo0 is bit 8). Use two markers to measure the high time: it is 4 µs.

## 4. Step through it

```bash
uv run piolab debug --scenario piolab.examples.pulse_counter:blink_scenario
(piolab) info program          # the 2 instructions
(piolab) step                  # one instruction (stops before the next issues)
(piolab) info pins             # led=1
(piolab) stepi 25              # 25 sysclk cycles = 1 SM cycle
(piolab) info cores            # pc and the stall reason ("delay")
(piolab) continue 1000
```

The same program also runs without a scenario: `uv run piolab sim piolab/examples/blink.pio --cycles 2000 --trace`. Devices are attached with `--device` specs, for example `--device uart:baud=115200,send=0x55` (see `piolab sim --help`).

In VS Code, open `blink.pio`, add this configuration to `.vscode/launch.json`, and press F5:

```json
{ "type": "piolab", "request": "launch", "name": "Blink",
  "scenario": "piolab.examples.pulse_counter:blink_scenario",
  "vcd": "${workspaceFolder}/build/debug.vcd", "stopOnEntry": true }
```

## Exercises
1. Make the duty cycle 25 %: high for 4 SM cycles, low for 12. Check it in GTKWave.
2. Remove `.clkdiv`. What is the period now? Why does the scenario still finish?
3. Replace `.wrap` with an explicit `jmp`. What happens to the period, and why is `.wrap` "zero-cost"?

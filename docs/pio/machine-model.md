# The RP2040 PIO model: what it implements

**Audience:** anyone writing PIO programs for the bench, or comparing the model with real silicon. The instruction semantics are in [isa.md](isa.md), generated from `pioscope/packs/pio/isa.yaml`, and the encoding is in [isa-encoding.html](isa-encoding.html). The code is `pioscope/dut/pio/`, on the generic core (`pioscope/dut/core`, [reference/simulator.md](../reference/simulator.md)).

## 1. What is modelled

| Part | Model | RP2040 |
|---|---|---|
| Instruction set | all nine instructions, every source/destination/condition, side-set (`opt`, `pindirs`), delay | same |
| Encoding | exact 16-bit words; tests compare with pioasm's output and the datasheet's field tables | same |
| State machines | one per program (`sm0`) | four per block |
| Instruction memory | 32 words | 32 words shared by the four SMs |
| FIFOs | 4 + 4 words of 32 bits; `fifo=tx`/`rx` joins them into 8 | same |
| Shift registers | OSR/ISR with counters, `in_shift`/`out_shift` left or right, autopull/autopush at a threshold of 1..32 | same |
| `mov status` | all ones while the chosen FIFO's level is below N | same |
| `out exec` / `mov exec` | the word runs on the next SM cycle; the PC does not move; the `out`/`mov`'s own delay is ignored | same |
| IRQ flags | 8 per block (a shared unit `irq`), `rel` adds the SM number mod 4; flags 0–3 are the host's interrupt lines | same |
| Clock divider | 16.8 fractional (`.clkdiv`) | same |
| Inputs | a 2-flop synchroniser on every input | same (can be bypassed per pin on silicon; not modelled) |
| Side-set vs `out`/`set`/`mov` on the same pin in the same cycle | side-set wins | same |

## 2. Pins

The bench is a Tiny Tapeout tile, so the model maps the tile's pads onto GPIO numbers:

| Pads | GPIO | Direction |
|---|---|---|
| `ui0`–`ui7` | 0–7 | input only |
| `uo0`–`uo7` | 8–15 | output only; reading one returns its driven level |
| `uio0`–`uio7` | 16–23 | bidirectional; driven when its direction bit is 1 |

The block's pin outputs and directions are a shared unit, `gpio` (24 bits each, in the VCD as `engine.shared.gpio.out` / `.oe`). `out`, `set`, `mov` and side-set write through the SM's pin mapping, from `.sm_config`:

| `.sm_config` key | Means | Default |
|---|---|---|
| `out=base[:count]` | `out pins`/`out pindirs`/`mov pins` write `count` pins from `base` (wrapping at 32) | the first output role in `.pins`, count 1 |
| `set=base[:count]` | `set pins`/`set pindirs` (count 0..5) | the first output role, count 1 |
| `in=pin` | `in pins`, `wait pin`, `mov x, pins` read from here | the first input role |
| `sideset=pin` | the first side-set pin | the first output role |
| `jmp=pin` | `jmp pin` | the first input role |
| `high=a\|b` | pins whose output level is 1 at reset | none |

A pin is a `.pins` role name or a GPIO number. At reset, every output role's direction is 1 (output) unless it is declared `/open_drain`. An open-drain role starts released, and a program drives it low by setting its direction with the output level at 0, as I²C on PIO does.

On an RP2040, the Pico SDK does all of this from C (`sm_config_set_*`, `pio_sm_set_consistent_pindirs`, `pio_sm_set_pins`). `.pins`, `.sm_config` and `.clkdiv` are harness directives that stand in for it. pioasm doesn't know them, and the optional pioasm cross-check strips them.

## 3. Timing

- Every instruction takes one SM cycle, plus its `[delay]`, unless it stalls (`wait`, a blocking `push`/`pull`, autopull/autopush on a full or empty FIFO, `irq wait`).
- The SM cycle is one sysclk enable from the fractional divider; the bench's sysclk is 50 MHz (an RP2040 usually runs at 125 MHz).
- An input change on a pad is seen by the SM 2 sysclk cycles later (the synchroniser). An output change appears on the bus 1 cycle after the instruction that made it.
- At a fast SM clock, the synchroniser delay matters. SPI mode 0 at a 50 MHz SM clock samples MISO before the slave's new bit has passed the synchroniser, as it would on silicon. The SPI programs use `.clkdiv 4`.

## 4. Not modelled (yet)

- More than one SM per block running different programs. An image holds one program. A model can add SMs that run the same one (see `TwoSms` in `pioscope/packs/pio/tests/test_debug.py`).
- pioasm's `.origin`, `.word`, `public` labels, `.lang_opt` and `% c-sdk` blocks; programs are relocated to address 0.
- Input synchroniser bypass, pin input/output inversion, FIFO debug flags (`FDEBUG`), and `SM_EXEC` from the host.
- RP2350 additions (`mov pindirs`, `irq prev/next`, `wait jmppin`, `mov rxfifo[]`). [Tutorial 06](../tutorials/06-isa-change.md) adds the first as an exercise.

## 5. Checking against silicon

- **Encoding:** `pioscope/packs/pio/tests/test_encode.py` compares the programs with pioasm's output and every instruction form with the datasheet's field tables. A Hypothesis test round-trips every 16-bit word.
- **Behaviour:** `test_exec.py` has a directed test per instruction and option. `test_scenarios.py` runs UART, SPI (modes 0–3) and I²C against the bench's device models and independent decoders, with timing checks.
- **Optional:** if `pioasm` (Pico SDK) is on the PATH, `test_against_installed_pioasm` assembles the same programs with it.

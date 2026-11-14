# RP2040 PIO ISA reference (generated)

RP2040 PIO. Generated from `pioscope/packs/pio/isa.yaml` (version 1.0.0). Do not edit by hand: run `uv run pioscope isa-docs`. Encoding: [isa-encoding.html](isa-encoding.html).

## Instructions

| Syntax | Modifiers | Status | Semantics |
|---|---|---|---|
| `jmp [cond], target` | delay, side | stable | If cond is true, set the PC to target; otherwise continue. x--/y-- decrement the register even when the jump is not taken. |
| `wait pol, src, idx, [rel]` | delay, side | stable | Stall until the source equals pol: gpio = absolute GPIO idx, pin = input pin idx (relative to the in base), irq = IRQ flag idx (0-7; rel adds the SM number modulo 4). A satisfied `wait 1 irq` clears the flag. |
| `in src, count` | delay, side | stable | Shift count bits from src into ISR (direction from `.sm_config in_shift=`) and add count to the input shift count (saturating at 32). With autopush, push ISR when the count reaches the threshold, stalling while the RX FIFO is full. |
| `out dst, count` | delay, side | stable | Shift count bits out of OSR (direction from `.sm_config out_shift=`) to dst and add count to the output shift count. pc jumps; exec runs the data as an instruction next cycle. With autopull, refill OSR from the TX FIFO when the count reaches the threshold, stalling while it is empty. |
| `push [iffull], [mode]` | delay, side | stable | Push ISR to the RX FIFO and clear it. iffull: do nothing unless the input count has reached the push threshold. block (default): stall while the FIFO is full; noblock: drop the data instead. |
| `pull [ifempty], [mode]` | delay, side | stable | Load OSR from the TX FIFO. ifempty: do nothing unless the output count has reached the pull threshold. block (default): stall while the FIFO is empty; noblock: copy X into OSR instead. With autopull on, a pull is a no-op while OSR is full. |
| `mov dst, src` | delay, side | stable | dst = op(src). op: ! or ~ inverts, :: bit-reverses. status is all ones while the selected FIFO's level is below N (`.sm_config status=`), else zero. mov isr/osr reset the matching shift count; exec runs the value as an instruction next cycle. |
| `irq [op], n, [rel]` | delay, side | stable | set / nowait: raise IRQ flag n. wait: raise it, then stall until something clears it. clear: lower it. rel adds the SM number modulo 4 to n's low two bits. |
| `set dst, value` | delay, side | stable | Write a 5-bit immediate to X or Y (zero-extended), or to the SM's set pins / their directions. |
| `nop` | delay, side | stable | Assembles to `mov y, y`: no effect beyond side-set and delay. |

## Conditions

| Condition | Semantics |
|---|---|
| `always` | Always (no condition written). |
| `!x` | X is zero. |
| `x--` | X is non-zero before the decrement; X is decremented either way. |
| `!y` | Y is zero. |
| `y--` | Y is non-zero before the decrement; Y is decremented either way. |
| `x!=y` | X differs from Y. |
| `pin` | The SM's jump pin (`.sm_config jmp=`) is high. |
| `!osre` | OSR is not empty: its shift count is below the pull threshold. |

## Directives

| Syntax | Semantics |
|---|---|
| `.program name` | Start of a program. One per source file (tool limitation; pioasm allows several). |
| `.side_set count, [opt], [pindirs]` | Side-set width in bits. opt makes it optional per instruction (costs an enable bit); pindirs side-sets pin directions instead of levels. Side-set and opt bits come out of the 5-bit delay field. |
| `.wrap_target` | Address the program wraps back to. |
| `.wrap` | After the preceding instruction, continue at .wrap_target at no cost. |
| `.define name, value ...` | Textual constant substituted for later operand items and inside expressions. |

## Harness directives

Configuration the bench needs that the ISA's own assembler does not have (for example what a C SDK sets up).

| Syntax | Semantics |
|---|---|
| `.pins map ...` | Harness: name the pads the SM uses, `.pins tx=uo0 sda=uio1/open_drain`. A pad is GPIO ui0-7 = 0-7, uo0-7 = 8-15, uio0-7 = 16-23. An output role starts as an output (pindir 1) unless open_drain. |
| `.sm_config [out=<pin_range>], [set=<pin_range>], [in=<pin_ref>], [sideset=<pin_ref>], [jmp=<pin_ref>], [out_shift=<shift_dir>], [in_shift=<shift_dir>], [autopull=<thresh>], [pull_thresh=<thresh>], [autopush=<thresh>], [push_thresh=<thresh>], [fifo=<fifo_join>], [status=<status_sel>], [high=<pin_set>]` | Harness: the SM configuration the Pico SDK sets from C (sm_config_set_*, and the pin init before the SM starts). Pins are .pins roles or GPIO numbers: out=base[:count] (default 0:32), set=base[:count] (default 0:5), in=, sideset=, jmp=. Shifts default to right. autopull=N / autopush=N turn on autopull / autopush at threshold N; pull_thresh= / push_thresh= set the threshold alone (default 32). fifo=tx|rx joins both FIFOs into one of depth 8. status=tx<N|rx<N selects mov status. high=a|b sets those pins' output level to 1 at reset (e.g. a UART line or a CPOL=1 clock idling high). |
| `.clkdiv div` | Harness: the SM clock divider, int[.frac] with 1 <= int <= 65535 and the fraction in 1/256 steps. Default 1 (every sysclk). |

## Encoding

16-bit word, MSB first: opcode (3) \| delay_side (5) \| operand (8).

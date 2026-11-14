# ADR-0008: `.clkdiv` directive (core clock divider)

Status: accepted (2026-11-11; ISA change approved by the owner per CLAUDE.md §8 #2)

## Context
Each core runs at sysclk through an integer/fractional divider, as on PIO. Bit-level programs take their bit rate from instructions-per-bit × divider. No directive set the divider, so a UART transmitter with 8 core cycles per bit ran at 6.25 Mbaud at 50 MHz and couldn't pass its 115200-baud scenario. On an RP2040, the Pico SDK sets the divider from C.

## Decision
- **Directive:** `.clkdiv <int>[.<frac>]`, compiled to `config["clkdiv"] = {"int": n, "frac": f}`. It is a generic directive (`pioscope.asm.directives`); the PIO pack marks it `harness: true`.
  - n is 1..65535. The decimal fraction is rounded to 1/256 steps, as in the RP2040's 16.8 divider (`.clkdiv 54.25` → frac 64).
  - A fraction that rounds to 256/256 carries into the next whole divider.
- **Engine core:** the core advances on an enable from a fractional accumulator: +256 per sysclk, an enable each time it reaches int·256 + frac. The average rate is exactly sysclk / div.
- **Default:** 1, an enable on every sysclk.

## Consequences
- The PIO `uart_tx`/`uart_rx` use `.clkdiv 54.25`: 50 MHz / (8 × 54.25) = 115,207 baud (+0.006%).
- SPI programs set their SCK rate the same way.
- The config image gains `clkdiv`, and an RTL engine will need a divider register for it.

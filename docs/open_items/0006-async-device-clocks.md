# ADR-0006: Asynchronous device clocks and a sampling window at the DUT pins

Status: accepted (2026-11-10)

## Context
The engine is a single-sysclk synchronous design, and every external signal enters through input flops. Rounding device edges to sysclk is therefore physically right. But the bench used to fix where an edge fell relative to the sampling edge, and it never resolved near-edge inputs either way. It also only allowed partner clocks that are whole-number fractions of sysclk. Those effects decide whether UART RX sample points, SPI-slave oversampling and CDR claims hold.

## Decision
- **Device clocks.** A device can run on its own `DeviceClock(freq_hz, ppm, offset_s, jitter_rms_s, seed)` (`pioscope/devices/clock.py`).
  - Jitter is per-edge, non-cumulative, Gaussian, clamped to ±3σ, and a pure function of `(seed, n)`.
  - Users so far: `UartPartner` (always: tick n is bit boundary n; `UartConfig.offset_s` and `jitter_rms_s`), `SpiMaster(sck_hz=…)` and `I2cMaster(scl_hz=…)`.
  - Slaves stay reactive.
  - Integer-cycle masters (`half_period`) are unchanged.
- **Timed edges.** `Device.drive(port, v, t=…)` stamps a change with its exact time inside the current DUT cycle. Scripts use `yield At(t)`. The waveform keeps whole-cycle `changes`, which is what decoders use. It also keeps `frac[net][cycle]`, the position within the cycle, which the VCD export uses for exact timestamps.
- **Sampling window.** `Scenario.sample_aperture_s` sets a setup window `(edge − aperture, edge]` before each DUT sampling edge.
  - A timed change on a `ui`/`uio` input net inside the window resolves, seeded by `Scenario.seed`, to old or new. "Old" means the DUT sees it one cycle late.
  - The window sits before the edge rather than around it, so the model stays causal. Centring it would only add a constant sub-cycle latency.
  - The pseudo-nets `dut:<net>` record what the DUT actually sampled.
  - `Result.stats` counts `metastable_windows`, `late_samples` and `subcycle_pulses_lost`. The last is two changes on one port within one cycle, which a synchronous sampler aliases.
- **Shared logic.** The `Board` makes every one of these decisions, so the Python and RTL backends stay identical. `parity_async_scenario` proves it on Icarus and Verilator, including `stats`.

## Scope / not modelled
- The window models only first-flop resolution at the pad. The engine's own 2-flop synchroniser latency is a DUT-internal detail and belongs in the engine model.
- There is no multi-clock DUT. If the engine ever gets a second clock domain (e.g. clocked by an external SCK), this needs a time-ordered scheduler.
- Device-side sampling (a partner reading DUT outputs) is ideal, at the cycle containing the partner's sample instant.

## Consequences
- With aperture 0 and untimed drives, results are bit-identical to before (`pioscope/tests/bench/test_async.py::test_synchronous_runs_are_bit_identical_to_before`).
- `UartPartner` TX edges now land at their exact times instead of the nearest cycle. Some UART waveforms moved by under one cycle; decoded data is unchanged.
- A UART RX tolerance sweep (ppm × 8 phases × jitter × window) becomes the "tolerated baud error" metric of an RX program on an engine model.

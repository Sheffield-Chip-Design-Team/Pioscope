# Verification bench

This is the environment around an engine: an assembler, a board of nets, device models, independent decoders, protocol scenarios, and two interchangeable backends. The DUT slot holds an ISA pack's engine model (the PIO model, for the examples here), a stand-in stub, or RTL through cocotb.

```
 piolab/packs/<pack>/programs/* ──piolab asm──▶ ProgramImage (IR + words + config)
                                   │ Loader (PyImageLoader | MemFileLoader → +PROG/+CFG)
                                   ▼
            ┌────────────── DUT slot (TT pins: ui / uo / uio+oe) ──────────────┐
            │ StubDut · a pack's Python engine model · RTL via cocotb             │
            └───────────────────────────────┬───────────────────────────────────┘
                                            │  Board: nets, pulls, wired-AND, conflicts
                 ┌──────────────┬───────────┼──────────────┬─────────────┐
            UartPartner     SpiSlave     I2cSlave      I2cMaster    SpiMaster ...
                                            │
                                     Waveform + device logs ─▶ Result
                                            │
                          decoders (piolab.decoders, independent) + checks (piolab.checks)
```

## Commands

```sh
uv sync                                   # create .venv (Python 3.13, cocotb 2.0.1)
uv run pytest                             # everything, Python backend
uv run pytest --backend=rtl               # same scenarios through cocotb + Icarus
uv run pytest --backend=rtl --sim=verilator
uv run pytest --backend=rtl --waves       # also dump sim_build/piolab_<sim>/runs/<scenario>/tb.fst
uv run pytest --update-golden             # accept listing / error-message changes

uv run piolab asm uart_tx --listing --hex build/mem       # build/uart_tx.json + build/uart_tx.lst
uv run piolab sim build/uart_tx.json --device uart:baud=115200 --host-tx 0x41,0x42 --vcd
uv run piolab rtl --scenario piolab.bench.parity:parity_scenario --sim icarus
uv run piolab --isa pio metrics               # static per-program metrics (piolab/scripts/metrics.py)
uv run piolab --isa pio isa-docs              # regenerate docs/pio/isa.md + isa-encoding.adoc/.html
uv run piolab --isa pio arch --write          # regenerate docs/pio/arch-study.md + .html (ADR-0007)
uv run piolab sim uart_rx --device uart:baud=115200,send=0x55 --trace
uv run piolab debug uart_rx --device uart:baud=115200,send=0x55   # docs/debugging.md
uv run pytest piolab/packs/pio/tests/test_scenarios.py   # PIO programs on the PIO model
```

Scenarios that need an engine (`requires_engine`, the default) run on the engine model of their program's ISA pack (`--dut auto`, the default). With an explicit stand-in (`--dut stub`), or RTL not flagged `--rtl-engine`, they **skip** with the reason "no engine". The other tests cover:
- the assembler;
- the board;
- device↔device loopbacks for UART, SPI and I²C, including stretching, NACK and arbitration;
- CAN node↔node loopbacks: arbitration, ACK, injected stuff/CRC/form/bit errors, error-passive and bus-off, clock tolerance (`piolab/tests/devices/test_can.py`);
- a CAN controller behind a `CanTransceiver` (TXD/RXD pins, loop delay, dominant time-out): how a DUT that is a CAN controller is wired to the bus;
- decoders and checks;
- backend parity.

## Timestamped logs

Every device transaction and every host FIFO push or pop is logged with its cycle and its exact time. Events on a device's own clock carry sub-cycle times. `piolab sim` and `piolab rtl` print one merged timeline:

```
             time       cycle  source        event       data
        2.0000 us  @      100  tx            sent        value=0x55 ...
       11.5200 us  @      576  rx            rx          value=0x55 parity_ok=True framing_ok=True ...
```

```sh
uv run piolab sim --scenario piolab.tests.devices.loopbacks:i2c_link --log-limit 0     # whole timeline
uv run piolab sim --scenario piolab.tests.devices.loopbacks:i2c_link --source m        # one device (or 'host')
uv run piolab sim --scenario piolab.tests.devices.loopbacks:i2c_link --trace           # live, as it runs
uv run piolab sim ... --log build/run.log                                       # full log to a file
uv run piolab rtl --scenario piolab.tests.devices.loopbacks:spi_link --trace           # live, in the cocotb log
```

- Bench time starts at reset release. cocotb's own simulator-time prefix also counts the reset cycles.
- When a test using the `bench` fixture fails, pytest appends a "bench timeline" section to the failure report.
- From Python, use `piolab.bench.report.render_log(result)`, `timeline(result)`, or `run_python(sc, dut, on_event=callback)`.

## Dropping in a DUT

**Python engine model.** Subclass `piolab.dut.core.Engine` (the observability contract comes with it; [reference/simulator.md](reference/simulator.md)), or implement the `piolab.dut.base.Dut` protocol directly:
- `load(image)`
- `reset() -> PadOut`
- `step(PadIn) -> PadOut` (one rising edge)
- `host_push` / `host_pop` / `host_irq` / `probes`
- `is_engine = True`

Then run:

```sh
uv run pytest --model fast            # one of the pack's models
uv run pytest --dut mypkg.model:Engine  # any model
uv run piolab sim build/uart_tx.json --dut mypkg.model:Engine --device uart:baud=115200 --host-tx 0x55
```

Before trusting results, pass the parity test with your DUT (see ADR-0001).

**RTL.** Keep the Tiny Tapeout top-level ports (`tt_um_*`: `ui_in`, `uo_out`, `uio_*`, `clk`, `rst_n`, `ena`); the packaged harness `piolab/bench/rtl/tb.v` instantiates it. For a whole Tiny Tapeout project (GDS, FPGA and docs actions, the TT cocotb flow), start from the `piolab-tt-template` repository. Then:

```sh
uv run pytest --backend=rtl --rtl-src src/engine.v --rtl-src src/top.v --rtl-engine
```

To make the backdoor loader populate the RTL instruction memory, define `PIOLAB_IMEM_PATH` for `tb.v` (e.g. `defines={"PIOLAB_IMEM_PATH": "tb.user_project.imem"}` in `run_rtl`). Then implement an `RtlHost` that reaches the FIFOs.

## Writing a scenario

A scenario is a **module-level** function returning `piolab.bench.Scenario`. It must be module-level so the cocotb process can rebuild it by import path. Pass parameters with `functools.partial(factory, key=value)`.

```python
def uart_tx(prog="uart_tx", payload=(0x55,)):
    image = program(prog)                                 # assembled source
    partner = UartPartner("partner", UartConfig(115_200))
    return Scenario(name=..., image=image, wiring=Wiring.from_image(image),
                    devices=[Attach(partner, {"rx": "tx"})],   # device port -> net
                    host=HostScript(tx={0: list(payload)}), max_cycles=...)

def test_tx(bench):                       # bench fixture picks the backend
    r = bench(uart_tx)
    frames = decode_uart(r.waveform, "tx", 115_200)        # independent decoder
    expect_sequence("decoded", [0x55], [f.value for f in frames])
```

Check only the `Result` (waveform, `r.txns(device, kind)`, `r.host_rx`), never live device objects. That keeps the test backend-neutral.

## Asynchronous clocks and the sampling window

By default every device edge lands on a whole DUT cycle. To model a partner with its own clock, see ADR-0006.

```python
UartPartner("p", UartConfig(115_200, ppm=-15_000, offset_s=7e-9, jitter_rms_s=1e-9))
SpiMaster("m", frames, mode=1, sck_hz=12.34e6, offset_s=3e-9, jitter_rms_s=0.3e-9)
I2cMaster("m", ops, scl_hz=97.3e3, jitter_rms_s=20e-9)
Scenario(..., sample_aperture_s=0.5e-9, seed=1)   # setup window at the DUT input flops
```

- Edges keep their exact time. The VCD shows them between the 20 ns ticks.
- With a window set, `dut:<net>` shows what the DUT actually sampled: the same edge, or one cycle later when it fell inside the window. Which of the two happens is seeded, so runs are reproducible.
- `r.stats` counts `metastable_windows`, `late_samples` and `subcycle_pulses_lost`. A lost pulse means a device clock faster than sysclk/2 squeezed two changes into one cycle.
- Sweep `offset_s` over one sysclk period (`k * 20e-9 / 8`) to cover all arrival phases. `piolab/packs/pio/tests/test_scenarios.py::test_uart_rx_8n1_with_baud_error` sweeps baud error; add `offset_s` to sweep phase.

```sh
uv run piolab sim --scenario piolab.bench.parity:parity_async_scenario --dut tt_template --vcd build/async.vcd
gtkwave build/async.vcd      # compare u_tx with dut_u_tx
```

**Seeing the partner clocks.** Every device with its own clock is drawn in the VCD under the `bench.clocks` scope, at exact times:
- `uart_bit_clk`: one pulse per bit boundary of a UART partner.
- `spi_sck_clk`: the SPI master's free-running SCK oscillator. The bus `sck` only runs inside frames.

These traces are display-only, so decoders never see them. Turn them off with `Scenario(record_device_clocks=False)`. In Python they are `r.waveform.clocks["clk:<device>.<name>"]`, a list of `(seconds, level)` pairs. Integer-cycle masters (`half_period=…`) have no separate clock: their bus SCK/SCL is the clock.

In custom scripts, `yield At(t)` resumes at absolute time `t` in seconds. Drives after it are stamped with `t`.

## Writing a device

Subclass `piolab.devices.Device`, declare `ports`, and implement `tick()`. Use `self.read(port)` and `self.drive(port, 0|1|Z)`. Bus masters are easier as a `ScriptedDevice` generator: `yield n` waits n cycles, and `yield lambda: cond` waits until the condition holds.

Device rules:
- Derive time from `self.cycle / self.sysclk_hz`.
- Use only `self.rng` for randomness (it is seeded).
- Log with `self.emit(kind, **data)`.
- Never use cocotb triggers.

Decoders must import only `piolab.wave`, and a test enforces this.

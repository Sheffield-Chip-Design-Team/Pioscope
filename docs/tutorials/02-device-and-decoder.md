# Tutorial 02: a device model, a decoder and a test

**You will:** write the other end of a wire (a device), an independent checker (a decoder), a scenario that wires them to a program, and a test. These four pieces make up every protocol work package (CAN, LIN, USB, …). **Code:** [`pioscope/examples/pulse_counter.py`](../../sim/examples/pulse_counter.py).

## The four pieces

| Piece | Role | Rules |
|---|---|---|
| **Device** (`PulseCounter`) | plays the other side of the bus: listens, answers, injects faults | subclass `pioscope.devices.base.Device`; declare `ports`; implement `tick()`; `self.read(port)`, `self.drive(port, 0/1/Z)`, `self.emit(kind, **data)`; time = `self.cycle`; randomness only from `self.rng` |
| **Decoder** (`decode_pulses`) | turns a recorded waveform into protocol data | imports **only** `pioscope.wave`, so it shares no code with the engine or devices and can't share their bugs |
| **Scenario** (`blink_scenario`) | wires program + devices + host script + stop condition | a module-level function returning `Scenario`, so the cocotb (RTL) backend can rebuild it by name |
| **Test** | checks the `Result` only | `pioscope/tests/examples/test_examples.py` |

## Read the device

`PulseCounter.tick()` runs once per sysclk cycle. It compares the level on `in` with the previous one: a rising edge records the cycle, and a falling edge emits `pulse` with the width. It never drives, so it is a pure listener. A UART partner, by contrast, also drives its TX port.

## Read the decoder

`decode_pulses(wave, "led")` uses `wave.edges(net)`, the list of (cycle, new level), and pairs rises with falls. The device and the decoder see the same wire but share no code. If they disagree, one of them is wrong.

## Read the scenario

```python
Scenario(name="blink", image=image, wiring=Wiring.from_image(image),
         devices=[Attach(counter, {"in": "led"})],      # device port "in" on net "led"
         max_cycles=(pulses + 2) * 400,
         until=lambda s: <counter has logged `pulses` pulses>)
```

`Wiring.from_image` makes one net per `.pins` role, named after the role.

## Read the test

```python
r = run_python(blink_scenario(5), PioEngine())
pulses = decode_pulses(r.waveform, "led")
assert all(p.width == 200 for p in pulses)
assert [t.data["width"] for t in r.txns("counter", "pulse")][:5] == [200] * 5
```

Run it: `uv run pytest pioscope/tests/examples -v`.

## Exercises
1. Add a `min_width` argument to `PulseCounter` and emit `glitch` for shorter pulses. Change `blink.pio` to make a 1-cycle pulse and test for it.
2. Make the decoder report the duty cycle. Write a Hypothesis test that varies the `[delay]` values in a generated program and checks the duty cycle.
3. Read `pioscope/devices/uart.py` and `pioscope/decoders/uart.py`. Find where the partner's baud-rate error is applied, and why the decoder doesn't know about it.

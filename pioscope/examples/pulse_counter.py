"""Tutorial 02: a device model, an independent decoder and a scenario.

- `PulseCounter` is a *device*: the other end of the wire. It watches one port and
  logs a `pulse` event (with its width in cycles) at every falling edge.
- `decode_pulses` is a *decoder*: it reads only the recorded waveform. Like every
  decoder it imports nothing but `pioscope.wave`, so a bug in the device or the engine
  can't hide itself by being shared with the checker.
- `blink_scenario` wires `blink.pio` to the device and stops after N pulses.

Run it:  uv run pioscope sim --scenario pioscope.examples.pulse_counter:blink_scenario \
             --dut engine --vcd build/blink.vcd
"""

from __future__ import annotations

from dataclasses import dataclass

from pioscope.asm import assemble_file
from pioscope.bench import Attach, Scenario, Wiring
from pioscope.devices.base import Device
from pioscope.wave import Waveform

from . import EXAMPLES_DIR


class PulseCounter(Device):
    """Counts high pulses on port `in`. It only listens: it never drives."""

    ports = ("in",)

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.last = 0
        self.rose_at: int | None = None
        self.count = 0

    def tick(self) -> None:
        level = self.read("in")
        if level == 1 and self.last == 0:
            self.rose_at = self.cycle
        if level == 0 and self.last == 1 and self.rose_at is not None:
            self.count += 1
            self.emit("pulse", n=self.count, width=self.cycle - self.rose_at)
        self.last = level if level in (0, 1) else self.last


@dataclass
class Pulse:
    rise: int  # cycle of the rising edge
    width: int  # cycles high
    period: int | None  # cycles to the next rising edge


def decode_pulses(wave: Waveform, net: str) -> list[Pulse]:
    """Every complete high pulse on `net`, from the waveform alone."""
    rises = [c for c, lvl in wave.edges(net) if lvl == 1]
    falls = [c for c, lvl in wave.edges(net) if lvl == 0]
    out = []
    for i, r in enumerate(rises):
        f = next((c for c in falls if c > r), None)
        if f is None:
            break
        nxt = rises[i + 1] if i + 1 < len(rises) else None
        out.append(Pulse(r, f - r, None if nxt is None else nxt - r))
    return out


def blink_scenario(pulses: int = 5) -> Scenario:
    image = assemble_file(EXAMPLES_DIR / "blink.pio")
    counter = PulseCounter("counter")
    return Scenario(
        name="blink",
        image=image,
        wiring=Wiring.from_image(image),
        devices=[Attach(counter, {"in": "led"})],
        max_cycles=(pulses + 2) * 400,
        until=lambda s: len([t for t in s.devices["counter"].log if t.kind == "pulse"])
        >= pulses,
    )

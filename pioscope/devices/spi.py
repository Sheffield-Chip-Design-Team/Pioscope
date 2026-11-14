"""SPI slave (all four modes) and an SPI master BFM.

Mode = (CPOL << 1) | CPHA. The leading edge is the idle->active SCK transition.
CPHA=0: data is sampled on the leading edge and changed on the trailing edge.
CPHA=1: data is changed on the leading edge and sampled on the trailing edge.

Logged transactions:
    word    {mosi, miso}   one complete word
    select  {} / deselect {bits}   CS edges (bits = partial bits at deselect)
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from pioscope.wave import Z

from .base import At, Device, Script, ScriptedDevice, Wait
from .clock import DeviceClock


def _bit(word: int, i: int, bits: int, msb_first: bool) -> int:
    return (word >> (bits - 1 - i if msb_first else i)) & 1


class SpiSlave(Device):
    ports = ("sck", "mosi", "miso", "cs")

    def __init__(
        self,
        name: str,
        mode: int = 0,
        bits: int = 8,
        msb_first: bool = True,
        responses: Iterable[int] = (),
        use_cs: bool = True,
        cs_active: int = 0,
        idle_response: int = 0xFF,
        seed: int = 0,
    ) -> None:
        super().__init__(name, seed)
        if mode not in range(4):
            raise ValueError("SPI mode must be 0..3")
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.bits, self.msb_first = bits, msb_first
        self.responses = list(responses)
        self.use_cs, self.cs_active = use_cs, cs_active
        self.idle_response = idle_response
        self._prev_sck = self.cpol
        self._selected = not use_cs
        self._n, self._rx = 0, 0
        self._tx: int | None = None  # None = no response loaded yet
        if not use_cs:
            self._reset_word()  # no CS: the first word is loaded up front

    def _reset_word(self) -> None:
        self._n = 0
        self._rx = 0
        self._tx = self.responses.pop(0) if self.responses else self.idle_response

    def _present(self) -> None:
        """Put bit n of the current word on MISO."""
        if self._tx is not None and self._n < self.bits:
            self.drive("miso", _bit(self._tx, self._n, self.bits, self.msb_first))

    def on_attach(self) -> None:
        if self._selected and self.cpha == 0:
            self._present()

    def tick(self) -> None:
        if self.use_cs:
            sel = self.read("cs") == self.cs_active
            if sel and not self._selected:
                self._selected = True
                if self._tx is None or self._n:
                    self._reset_word()  # first select, or a partial word was abandoned
                self.emit("select")
                if self.cpha == 0:
                    self._present()
            elif not sel and self._selected:
                self._selected = False
                self.emit("deselect", bits=self._n)
                self.release("miso")
        sck = self.read("sck")
        if self._selected and sck in (0, 1) and sck != self._prev_sck:
            leading = sck != self.cpol
            if leading == (self.cpha == 0):  # sample edge
                b = self.read("mosi") & 1
                self._rx = (self._rx << 1 | b) if self.msb_first else self._rx | (b << self._n)
                self._n += 1
                if self._n == self.bits:
                    self.emit("word", mosi=self._rx, miso=self._tx)
                    self._reset_word()
            else:  # shift edge
                self._present()
        if sck in (0, 1):
            self._prev_sck = sck


class SpiMaster(ScriptedDevice):
    """Drives SCK/MOSI/CS and samples MISO. `frames` is a list of CS frames,
    each a list of words."""

    ports = ("sck", "mosi", "miso", "cs")

    def __init__(
        self,
        name: str,
        frames: Sequence[Sequence[int]],
        mode: int = 0,
        bits: int = 8,
        msb_first: bool = True,
        half_period: int = 4,
        cs_setup: int = 4,
        cs_gap: int = 8,
        start_delay: int = 4,
        sck_hz: float | None = None,
        ppm: float = 0.0,
        offset_s: float = 0.0,
        jitter_rms_s: float = 0.0,
        seed: int = 0,
    ) -> None:
        """SCK timing: `half_period` whole sysclk cycles, or, with `sck_hz`, the
        master's own asynchronous clock (ppm / offset / jitter); edges then land
        part-way through DUT cycles. cs_setup/cs_gap/start_delay are in cycles."""
        super().__init__(name, seed)
        self.frames = [list(f) for f in frames]
        self.cpol, self.cpha = mode >> 1, mode & 1
        self.bits, self.msb_first = bits, msb_first
        self.half, self.cs_setup, self.cs_gap = half_period, cs_setup, cs_gap
        self.start_delay = start_delay
        self.clock = (DeviceClock(2 * sck_hz, ppm, offset_s, jitter_rms_s, seed)
                      if sck_hz else None)
        self._h = -1  # index of the last half-period tick used
        self.drive("sck", self.cpol)
        self.drive("cs", 1)
        self.drive("mosi", 0)

    def script(self) -> Script:
        idle, active = self.cpol, 1 - self.cpol
        yield self.start_delay
        for frame in self.frames:
            self.drive("cs", 0)
            yield self.cs_setup
            if self.clock is not None:
                yield self._half()  # SCK edges stay on the master's own clock grid
            for word in frame:
                rx = 0
                for i in range(self.bits):
                    b = _bit(word, i, self.bits, self.msb_first)
                    if self.cpha == 0:
                        self.drive("mosi", b)
                        yield self._half()
                        rx = self._shift(rx, i)  # sample at the leading edge
                        self.drive("sck", active)
                        yield self._half()
                        self.drive("sck", idle)
                    else:
                        self.drive("sck", active)
                        self.drive("mosi", b)
                        yield self._half()
                        rx = self._shift(rx, i)  # sample at the trailing edge
                        self.drive("sck", idle)
                        yield self._half()
                if self.cpha == 0:
                    yield self._half()
                self.emit("word", mosi=word, miso=rx)
            yield self.cs_setup
            self.drive("cs", 1)
            yield self.cs_gap
        self.emit("done")

    def clocks(self) -> dict[str, tuple[DeviceClock, str]]:
        # free-running SCK of the master's own oscillator (the bus SCK only runs in frames)
        return {"sck_clk": (self.clock, "toggle")} if self.clock is not None else {}

    def _half(self) -> Wait:
        """Wait for half an SCK period: cycles, or the next tick of the own clock."""
        if self.clock is None:
            return self.half
        now = self.now()
        n = max(self._h + 1, math.floor((now - self.clock.offset_s) / self.clock.period_s))
        while self.clock.tick(n) <= now:
            n += 1
        self._h = n
        return At(self.clock.tick(n))

    def _shift(self, rx: int, i: int) -> int:
        b = self.read("miso") & 1 if self.read("miso") in (0, 1) else 0
        return (rx << 1 | b) if self.msb_first else rx | (b << i)


__all__ = ["SpiMaster", "SpiSlave", "Z"]

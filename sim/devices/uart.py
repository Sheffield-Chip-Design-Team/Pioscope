"""UART partner: the far end of a UART link, with its own (possibly wrong) clock.

Ports:
    tx  driven by the partner  (connect to the DUT's RX net)
    rx  listened to            (connect to the DUT's TX net)

Fault injection: baud error (ppm), random or explicit glitches on tx,
parity errors, framing errors (stop bit = 0) and breaks.

Logged transactions:
    rx      {value, parity_ok, framing_ok, start}   a frame received on `rx`
    break   {start, length_cycles}                 line held low past a frame
    sent    {value, start, ...}                     a frame started on `tx`
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sim.wave import X

from .base import Device
from .clock import DeviceClock


@dataclass(frozen=True)
class UartConfig:
    baud: float
    data_bits: int = 8
    parity: str = "none"  # none | even | odd
    stop_bits: float = 1  # 1, 1.5 or 2
    ppm: float = 0.0  # partner clock error; + = partner is fast
    offset_s: float = 0.0  # phase of the partner's bit clock (sweep over one sysclk period)
    jitter_rms_s: float = 0.0  # per-edge jitter of the partner's TX edges
    idle: int = 1
    lsb_first: bool = True

    def __post_init__(self) -> None:
        if self.parity not in ("none", "even", "odd"):
            raise ValueError(f"parity must be none/even/odd, not {self.parity!r}")
        if self.stop_bits not in (1, 1.5, 2):
            raise ValueError("stop_bits must be 1, 1.5 or 2")


@dataclass(frozen=True)
class UartSend:
    value: int = 0
    parity_error: bool = False
    framing_error: bool = False
    break_bits: float = 0  # >0: send a break of this many bit times instead of a frame
    gap_bits: float = 0  # idle time before this frame


def parity_bit(value: int, bits: int, kind: str) -> int:
    ones = bin(value & ((1 << bits) - 1)).count("1")
    return ones & 1 if kind == "even" else (ones & 1) ^ 1


class UartPartner(Device):
    ports = ("tx", "rx")

    def __init__(
        self,
        name: str,
        cfg: UartConfig,
        send: Iterable[int | UartSend] = (),
        start_delay_bits: float = 2.0,
        glitch_prob: float = 0.0,  # per transmitted data bit
        glitch_cycles: int = 1,
        glitches: Sequence[tuple[int, int]] = (),  # explicit (cycle, width) on tx
        seed: int = 0,
    ) -> None:
        super().__init__(name, seed)
        self.cfg = cfg
        self.to_send = [s if isinstance(s, UartSend) else UartSend(value=s) for s in send]
        self.start_delay_bits = start_delay_bits
        self.glitch_prob = glitch_prob
        self.glitch_cycles = glitch_cycles
        self.explicit_glitches = list(glitches)
        # tx schedule (built in on_attach)
        self._glitches: list[tuple[int, int]] = []
        self._sent_at: list[tuple[float, UartSend]] = []
        # rx state
        self._rx_state = "wait_idle"
        self._prev = cfg.idle
        self._rx_start = 0
        self._rx_bits: list[int] = []
        self._rx_next = 0.0

    @property
    def bit_cycles(self) -> float:
        return self.sysclk_hz / (self.cfg.baud * (1 + self.cfg.ppm * 1e-6))

    # ---- TX schedule ------------------------------------------------------------
    def on_attach(self) -> None:
        cfg = self.cfg
        # The partner's own clock: tick n = bit boundary n (asynchronous to sysclk).
        self.clock = DeviceClock(cfg.baud, cfg.ppm, offset_s=cfg.offset_s,
                                 jitter_rms_s=cfg.jitter_rms_s, seed=self.seed)
        period_c = self.clock.period_s * self.sysclk_hz
        u = self.start_delay_bits  # position in bit times
        lv: list[tuple[float, int]] = []
        for s in self.to_send:
            u += s.gap_bits
            self._sent_at.append((self.clock.tick(u), s))
            if s.break_bits:
                lv.append((u, 1 - cfg.idle))
                u += s.break_bits
                lv.append((u, cfg.idle))
                u += 1
                continue
            bits = [1 - cfg.idle]
            data = [(s.value >> i) & 1 for i in range(cfg.data_bits)]
            bits += data if cfg.lsb_first else data[::-1]
            if cfg.parity != "none":
                par = parity_bit(s.value, cfg.data_bits, cfg.parity)
                bits.append(par ^ 1 if s.parity_error else par)
            for i, b in enumerate(bits):
                lv.append((u + i, b))
                if 1 <= i <= cfg.data_bits and self.glitch_prob and \
                        self.rng.random() < self.glitch_prob:
                    t0 = self.clock.tick(u + i) * self.sysclk_hz
                    at = math.floor(t0 + self.rng.random() * (period_c - self.glitch_cycles))
                    self._glitches.append((at, self.glitch_cycles))
            u += len(bits)
            lv.append((u, 1 - cfg.idle if s.framing_error else cfg.idle))
            u += cfg.stop_bits
            lv.append((u, cfg.idle))
        self._glitches += list(self.explicit_glitches)
        self._glitches.sort()
        # Exact edge times; consecutive equal levels are merged.
        self._sched: list[tuple[float, int]] = []
        level = cfg.idle
        for pos, new_level in lv:
            if new_level != level:
                self._sched.append((self.clock.tick(pos), new_level))
                level = new_level
        self._next = 0
        self._base = cfg.idle
        self.tx_end = self.cycle_of(self.clock.tick(u))

    def clocks(self) -> dict[str, tuple[DeviceClock, str]]:
        return {"bit_clk": (self.clock, "tick")} if hasattr(self, "clock") else {}

    def _glitch_active(self, cycle: int) -> bool:
        j = bisect.bisect_right(self._glitches, (cycle, 1 << 30)) - 1
        while j >= 0 and self._glitches[j][0] + self._glitches[j][1] > cycle:
            if self._glitches[j][0] <= cycle:
                return True
            j -= 1
        return False

    # ---- per cycle ----------------------------------------------------------------
    def tick(self) -> None:
        if self._sent_at and self.cycle_of(self._sent_at[0][0]) <= self.cycle:
            t_start, s = self._sent_at.pop(0)
            self.emit("sent", at=t_start, value=s.value, parity_error=s.parity_error,
                      framing_error=s.framing_error, break_bits=s.break_bits)
        g = 1 if self._glitch_active(self.cycle) else 0
        timed = False
        while self._next < len(self._sched) and \
                self.cycle_of(self._sched[self._next][0]) <= self.cycle:
            t, self._base = self._sched[self._next]
            self._next += 1
            # an edge that lands inside this DUT cycle carries its exact time
            self.drive("tx", self._base ^ g, t=t if self.cycle_of(t) == self.cycle else None)
            timed = True
        if not timed:
            self.drive("tx", self._base ^ g)
        self._rx_tick(self.read("rx"))

    def _rx_tick(self, lvl: int) -> None:
        cfg, idle = self.cfg, self.cfg.idle
        st = self._rx_state
        if st == "wait_idle":
            if lvl == idle:
                self._rx_state = "idle"
        elif st == "idle":
            if lvl == 1 - idle and self._prev == idle:
                self._rx_start = self.cycle
                self._rx_bits = []
                self._rx_next = self.cycle + 0.5 * self.bit_cycles
                self._rx_state = "frame"
        elif st == "frame" and self.cycle >= math.floor(self._rx_next + 0.5):
            self._rx_bits.append(lvl)
            self._rx_next += self.bit_cycles
            n = 1 + cfg.data_bits + (cfg.parity != "none") + (2 if cfg.stop_bits == 2 else 1)
            if len(self._rx_bits) == 1 and lvl != 1 - idle:
                self._rx_state = "idle"  # false start (glitch)
            elif len(self._rx_bits) == n:
                self._finish_frame()
        elif st == "break" and lvl == idle:
            self.emit("break", start=self._rx_start, length_cycles=self.cycle - self._rx_start)
            self._rx_state = "idle"
        self._prev = lvl

    def _finish_frame(self) -> None:
        cfg, idle = self.cfg, self.cfg.idle
        # Normalise to an idle-high line (X stays X).
        bits = [b if idle == 1 or b == X else b ^ 1 for b in self._rx_bits]
        data = bits[1 : 1 + cfg.data_bits]
        if not cfg.lsb_first:
            data = data[::-1]
        has_x = X in self._rx_bits
        value = sum((b & 1) << i for i, b in enumerate(data))
        parity_ok = True
        idx = 1 + cfg.data_bits
        if cfg.parity != "none":
            parity_ok = bits[idx] == parity_bit(value, cfg.data_bits, cfg.parity)
            idx += 1
        stops = bits[idx:]
        framing_ok = all(b == 1 for b in stops)
        if all(b == 0 for b in bits):
            self._rx_state = "break"
            return
        self.emit("rx", value=value, parity_ok=parity_ok, framing_ok=framing_ok,
                  start=self._rx_start, x=has_x)
        self._rx_state = "idle" if framing_ok else "wait_idle"

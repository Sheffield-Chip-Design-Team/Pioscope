"""I2C decoder: START/STOP/bytes+ACK from SCL/SDA, plus SCL timing."""

from __future__ import annotations

from dataclasses import dataclass, field

from pioscope.wave import Waveform, X


@dataclass
class I2cEvent:
    cycle: int
    kind: str  # start | restart | stop | addr | data
    value: int | None = None
    ack: bool | None = None  # True = ACK (SDA low on 9th clock)
    rw: int | None = None  # addr events only
    t_low: list[int] = field(default_factory=list)  # SCL low periods within this byte
    t_high: list[int] = field(default_factory=list)


def decode_i2c(wf: Waveform, scl: str, sda: str) -> list[I2cEvent]:
    events: list[I2cEvent] = []
    cycles = sorted({c for c, _ in wf.edges(scl)} | {c for c, _ in wf.edges(sda)})
    in_txn = False
    bits: list[int] = []
    first_byte = False
    last_rise = last_fall = None
    lows: list[int] = []
    highs: list[int] = []
    byte_start = 0
    for c in cycles:
        s0, s1 = wf.at(scl, c - 1), wf.at(scl, c)
        d0, d1 = wf.at(sda, c - 1), wf.at(sda, c)
        if X in (s0, s1, d0, d1):
            continue
        if s0 == 1 and s1 == 1 and d0 != d1:
            if d1 == 0:
                events.append(I2cEvent(c, "restart" if in_txn else "start"))
                in_txn, first_byte, bits, lows, highs = True, True, [], [], []
            else:
                events.append(I2cEvent(c, "stop"))
                in_txn, bits = False, []
            continue
        if s0 == 0 and s1 == 1:
            if last_fall is not None:
                lows.append(c - last_fall)
            last_rise = c
            if not in_txn:
                continue
            if not bits:
                byte_start = c
            bits.append(d1)
            if len(bits) == 9:
                v = 0
                for b in bits[:8]:
                    v = (v << 1) | b
                ack = bits[8] == 0
                if first_byte:
                    events.append(I2cEvent(byte_start, "addr", v >> 1, ack, v & 1, lows, highs))
                    first_byte = False
                else:
                    events.append(I2cEvent(byte_start, "data", v, ack, None, lows, highs))
                bits, lows, highs = [], [], []
        elif s0 == 1 and s1 == 0:
            if last_rise is not None:
                highs.append(c - last_rise)
            last_fall = c
    return events

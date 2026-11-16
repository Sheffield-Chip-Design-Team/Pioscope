"""CAN 2.0 decoder: frames from one bus net (0 = dominant, 1 = recessive).

Written independently of the CAN device model (it imports only `pioscope.wave`):
- start of frame: a falling edge after at least `idle_bits` recessive bit times
  (or from the start of the capture);
- bit timing: nominal bit period from `bitrate`, sampled at `sample_point`, with a
  full resynchronisation on every recessive -> dominant edge;
- de-stuffing (a complementary bit after 5 equal bits, SOF to the end of the CRC,
  including a stuff bit straight after the last CRC bit) and CRC-15 (poly 0x4599);
- the tail: CRC delimiter, ACK slot, ACK delimiter and EOF are checked for form.

A frame that breaks off (stuff or form error, X on the line, end of capture) is
still reported, with `error` set and the fields decoded so far.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from pioscope.wave import Waveform, X

CRC15_POLY = 0x4599


def crc15(bits: list[int]) -> int:
    """CRC-15/CAN over a bit list, MSB first (init 0, no reflection, no xorout)."""
    reg = 0
    for b in bits:
        fb = b ^ ((reg >> 14) & 1)
        reg = (reg << 1) & 0x7FFF
        if fb:
            reg ^= CRC15_POLY
    return reg


@dataclass
class CanDecodedFrame:
    start: int  # cycle of the SOF edge
    id: int = 0
    ext: bool = False
    rtr: bool = False
    dlc: int = 0
    data: bytes = b""
    crc: int = 0  # as received
    crc_ok: bool = False
    acked: bool = False
    stuff_bits: int = 0
    #: None for a good frame, else "stuff" | "form" | "crc" | "x" | "truncated"
    error: str | None = None
    end: int = 0  # cycle of the last sample taken for this frame
    samples: list[int] = field(default_factory=list)  # sample cycle of every bit


def _num(bits: list[int]) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | b
    return v


def decode_can(
    wf: Waveform,
    net: str,
    bitrate: float,
    sample_point: float = 0.75,
    idle_bits: float = 10.5,
    start_cycle: int = 0,
) -> list[CanDecodedFrame]:
    bit = wf.sysclk_hz / bitrate
    edges = [(c, v) for c, v in wf.edges(net) if c >= start_cycle]
    falls = [c for c, v in edges if v == 0]
    frames: list[CanDecodedFrame] = []
    busy_until = start_cycle
    for c in falls:
        if c < busy_until or wf.at(net, c - 1) != 1:
            continue
        # recessive time before this edge: back to the previous change (or capture start)
        prev = [e for e, _ in edges if e < c]
        if prev and c - prev[-1] < idle_bits * bit:
            continue
        f = _decode_one(wf, net, bit, sample_point, c, falls)
        frames.append(f)
        busy_until = f.end + 1
    return frames


def _decode_one(wf: Waveform, net: str, bit: float, sp: float, sof: int,
                falls: list[int]) -> CanDecodedFrame:
    f = CanDecodedFrame(start=sof)
    bits: list[int] = []  # de-stuffed, SOF first
    run, last = 0, -1
    stuffing = True
    need: int | None = None  # de-stuffed length up to the end of the CRC
    hdr = 0
    tail = 0  # bits seen after the CRC (and its trailing stuff bit)
    start = float(sof)
    while True:
        s = int(start + sp * bit)
        if s >= wf.length:
            f.error = f.error or "truncated"
            return f
        lvl = wf.at(net, s)
        f.samples.append(s)
        f.end = s
        # resynchronise on the next falling edge before the next sample point
        nominal = start + bit
        i = bisect.bisect_right(falls, s)
        start = float(falls[i]) if i < len(falls) and falls[i] <= nominal + sp * bit else nominal
        if lvl == X:
            f.error = "x"
            return f
        if stuffing:
            if run == 5:  # this is a stuff bit
                if lvl == last:
                    f.error = "stuff"
                    return f
                f.stuff_bits += 1
                run, last = 1, lvl
                if need is not None and len(bits) == need:
                    stuffing = False
                continue
            run = run + 1 if lvl == last else 1
            last = lvl
            bits.append(lvl)
            if len(bits) == 14:
                f.ext = bits[13] == 1
                hdr = 39 if f.ext else 19
            if hdr and len(bits) == hdr:
                if f.ext:
                    f.id = (_num(bits[1:12]) << 18) | _num(bits[14:32])
                    f.rtr = bits[32] == 1
                else:
                    f.id = _num(bits[1:12])
                    f.rtr = bits[12] == 1
                f.dlc = _num(bits[hdr - 4 : hdr])
                need = hdr + (0 if f.rtr else 8 * min(f.dlc, 8)) + 15
            if need is not None and len(bits) == need:
                body = bits[: need - 15]
                f.data = bytes(_num(body[hdr + 8 * k : hdr + 8 * k + 8])
                               for k in range((len(body) - hdr) // 8))
                f.crc = _num(bits[need - 15 :])
                f.crc_ok = crc15(body) == f.crc
                if not f.crc_ok:
                    f.error = "crc"
                if run < 5:
                    stuffing = False
            continue
        # tail: CRC delimiter, ACK slot, ACK delimiter, EOF x 7
        tail += 1
        if tail == 2:
            f.acked = lvl == 0
        elif lvl == 0 and tail <= 9:  # the last EOF bit is not checked by receivers
            f.error = f.error or "form"
            return f
        if tail == 10:
            return f

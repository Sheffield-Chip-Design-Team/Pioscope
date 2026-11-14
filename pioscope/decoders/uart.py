"""UART decoder: frames from one net, plus measured bit timing."""

from __future__ import annotations

from dataclasses import dataclass, field

from pioscope.wave import Waveform, X


@dataclass
class UartFrame:
    start: int  # cycle of the start edge
    value: int
    parity_ok: bool
    framing_ok: bool
    is_break: bool = False
    has_x: bool = False
    edges: list[int] = field(default_factory=list)  # edge cycles inside the frame
    measured_bit_cycles: float | None = None


def _parity(v: int, bits: int, kind: str) -> int:
    ones = bin(v & ((1 << bits) - 1)).count("1") & 1
    return ones if kind == "even" else ones ^ 1


def decode_uart(
    wf: Waveform,
    net: str,
    baud: float,
    data_bits: int = 8,
    parity: str = "none",
    stop_bits: float = 1,
    idle: int = 1,
    lsb_first: bool = True,
    start_cycle: int = 0,
) -> list[UartFrame]:
    bit = wf.sysclk_hz / baud
    nbits = 1 + data_bits + (parity != "none")
    frame_len = nbits + stop_bits
    edges = [c for c, v in wf.edges(net) if c >= start_cycle]
    frames: list[UartFrame] = []
    i = 0
    busy_until = start_cycle
    while i < len(edges):
        c = edges[i]
        i += 1
        if c < busy_until or wf.at(net, c) != 1 - idle or wf.at(net, c - 1) != idle:
            continue
        # sample the centre of each bit
        mid = [c + int((k + 0.5) * bit) for k in range(nbits + (2 if stop_bits == 2 else 1))]
        if mid[-1] >= wf.length:
            break
        raw = [wf.at(net, m) for m in mid]
        if raw[0] != 1 - idle:
            continue  # glitch, not a start bit
        has_x = X in raw
        norm = [r if idle == 1 or r == X else r ^ 1 for r in raw]
        data = norm[1 : 1 + data_bits]
        if not lsb_first:
            data = data[::-1]
        value = sum((b & 1) << k for k, b in enumerate(data))
        parity_ok = True
        if parity != "none":
            parity_ok = norm[1 + data_bits] == _parity(value, data_bits, parity)
        stops = norm[nbits:]
        framing_ok = all(s == 1 for s in stops)
        end = c + int(frame_len * bit)
        inside = [e for e in edges if c <= e < c + int((nbits + 0.5) * bit)]
        fr = UartFrame(c, value, parity_ok, framing_ok, has_x=has_x, edges=inside)
        if all(b == 0 for b in norm):
            fr.is_break = True
            # a break lasts until the line returns to idle
            nxt = next((e for e in edges if e > c and wf.at(net, e) == idle), wf.length)
            end = nxt
        fr.measured_bit_cycles = _fit_bit(inside, c, bit)
        frames.append(fr)
        busy_until = end if fr.framing_ok or fr.is_break else c + int((nbits + 0.5) * bit)
    return frames


def _fit_bit(edges: list[int], start: int, nominal: float) -> float | None:
    """Least-squares bit period from edge times relative to the start edge."""
    pts = []
    for e in edges[1:]:
        k = round((e - start) / nominal)
        if k > 0:
            pts.append((k, e - start))
    if not pts:
        return None
    return sum(k * t for k, t in pts) / sum(k * k for k, _ in pts)

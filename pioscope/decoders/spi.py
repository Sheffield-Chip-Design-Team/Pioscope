"""SPI decoder: words on MOSI/MISO, plus SCK and setup/hold timing."""

from __future__ import annotations

from dataclasses import dataclass, field

from pioscope.wave import Waveform, X


@dataclass
class SpiWord:
    start: int  # cycle of the first sampling edge
    mosi: int | None
    miso: int | None
    sample_edges: list[int] = field(default_factory=list)
    min_setup: int | None = None  # cycles from last data change to sampling edge (MOSI)
    min_hold: int | None = None  # cycles from sampling edge to next data change (MOSI)
    has_x: bool = False


def decode_spi(
    wf: Waveform,
    sck: str,
    mosi: str | None,
    miso: str | None,
    cs: str | None = None,
    mode: int = 0,
    bits: int = 8,
    msb_first: bool = True,
    cs_active: int = 0,
) -> list[SpiWord]:
    cpol, cpha = mode >> 1, mode & 1
    words: list[SpiWord] = []
    cur: list[tuple[int, int, int]] = []  # (cycle, mosi bit, miso bit)
    xs = False

    def active(c: int) -> bool:
        return cs is None or wf.at(cs, c) == cs_active

    cs_edges = {e for e, _ in wf.edges(cs)} if cs else set()
    events = sorted({e for e, _ in wf.edges(sck)} | cs_edges)
    for c in events:
        if c in cs_edges and not active(c):
            cur, xs = [], False  # deselect drops a partial word
            continue
        if c in cs_edges and active(c):
            cur, xs = [], False
        lvl, prev = wf.at(sck, c), wf.at(sck, c - 1)
        if lvl in (prev, X) or prev == X or not active(c):
            continue
        leading = lvl != cpol
        if leading != (cpha == 0):
            continue
        mo = wf.at(mosi, c) if mosi else 0
        mi = wf.at(miso, c) if miso else 0
        xs = xs or X in (mo, mi)
        cur.append((c, mo, mi))
        if len(cur) == bits:
            words.append(_word(wf, cur, mosi, miso, msb_first, xs))
            cur, xs = [], False
    return words


def _pack(vals: list[int], msb_first: bool) -> int:
    v = 0
    for i, b in enumerate(vals):
        if msb_first:
            v = (v << 1) | (b & 1)
        else:
            v |= (b & 1) << i
    return v


def _word(wf: Waveform, cur: list[tuple[int, int, int]], mosi: str | None, miso: str | None,
          msb_first: bool, xs: bool) -> SpiWord:
    edges = [c for c, _, _ in cur]
    w = SpiWord(
        start=edges[0],
        mosi=_pack([b for _, b, _ in cur], msb_first) if mosi else None,
        miso=_pack([b for _, _, b in cur], msb_first) if miso else None,
        sample_edges=edges, has_x=xs,
    )
    if mosi:
        changes = [c for c, _ in wf.edges(mosi)]
        setups, holds = [], []
        for e in edges:
            before = [c for c in changes if c <= e]
            after = [c for c in changes if c > e]
            setups.append(e - before[-1] if before else e)
            if after:
                holds.append(after[0] - e)
        w.min_setup = min(setups)
        w.min_hold = min(holds) if holds else None
    return w

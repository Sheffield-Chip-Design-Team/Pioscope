"""Human-readable, timestamped logs of a run.

Every event (device transaction or host FIFO push/pop) is shown with its exact
time and its DUT cycle, merged across sources into one timeline:

         time_us     cycle  source        event       data
      12.3456 us  @    617  partner       rx          value=0x55 parity_ok=True ...
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sim.devices.base import Txn

from .scenario import Result


@dataclass(frozen=True)
class Event:
    t: float
    cycle: int
    source: str
    kind: str
    data: dict[str, Any]


def timeline(result: Result, sources: Iterable[str] | None = None) -> list[Event]:
    """All logged events, ordered by time (then cycle, source, log order)."""
    want = set(sources) if sources is not None else None
    evs = [Event(t.t, t.cycle, src, t.kind, t.data)
           for src, txns in result.logs.items() if want is None or src in want
           for t in txns]
    return sorted(evs, key=lambda e: (e.t, e.cycle))  # stable: keeps per-source order


def _fmt_value(k: str, v: Any) -> str:
    if isinstance(v, bool) or not isinstance(v, int):
        return f"{k}={v}"
    if k in ("value", "byte", "word", "mosi", "miso", "addr"):
        return f"{k}=0x{v:02x}"
    return f"{k}={v}"


def format_time(t: float) -> str:
    return f"{t * 1e6:14.4f} us"


def format_event(source: str, txn: Txn | Event) -> str:
    data = " ".join(_fmt_value(k, v) for k, v in txn.data.items())
    return f"{format_time(txn.t)}  @{txn.cycle:>9}  {source:12s}  {txn.kind:10s}  {data}"


HEADER = f"{'time':>17}  {'cycle':>10}  {'source':12s}  {'event':10s}  data"


def render_log(result: Result, limit: int | None = None,
               sources: Iterable[str] | None = None) -> str:
    evs = timeline(result, sources)
    shown = evs if limit is None or limit <= 0 else evs[:limit]
    lines = [HEADER] + [format_event(e.source, e) for e in shown]
    if len(shown) < len(evs):
        lines.append(f"... {len(evs) - len(shown)} more events (use --log-limit 0 or --log FILE)")
    return "\n".join(lines)

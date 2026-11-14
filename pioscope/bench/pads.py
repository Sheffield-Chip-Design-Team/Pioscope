"""The DUT pin contract: the Tiny Tapeout top-level ports, sampled once per sysclk."""

from __future__ import annotations

import re
from dataclasses import dataclass

GROUP_DIR = {"ui": "in", "uo": "out", "uio": "bidir"}
PAD_NAMES = [f"{g}{i}" for g in ("ui", "uo", "uio") for i in range(8)]
_PAD = re.compile(r"^(ui|uo|uio)([0-7])$")


def parse_pad(name: str) -> tuple[str, int]:
    m = _PAD.match(name)
    if not m:
        raise ValueError(f"unknown pad '{name}' (expected ui0..7, uo0..7, uio0..7)")
    return m.group(1), int(m.group(2))


@dataclass(frozen=True)
class PadIn:
    """Values presented to the DUT inputs. `*_x` masks mark unknown bits."""

    ui: int = 0
    uio_in: int = 0
    ui_x: int = 0
    uio_x: int = 0


@dataclass(frozen=True)
class PadOut:
    """DUT outputs. TT `uo` pads are always driven; `uio` only where `uio_oe` is set."""

    uo: int = 0
    uio_out: int = 0
    uio_oe: int = 0
    uo_x: int = 0
    uio_out_x: int = 0
    uio_oe_x: int = 0

"""GPIO numbering of the Tiny Tapeout pads for the PIO pack: ui0-7 = GPIO 0-7,
uo0-7 = GPIO 8-15, uio0-7 = GPIO 16-23 (piolab/packs/pio/isa.yaml `target`)."""

from __future__ import annotations

GROUP_BASE = {"ui": 0, "uo": 8, "uio": 16}
NUM_GPIO = 24


def gpio_of(pad: str) -> int:
    """`uio3` -> 19."""
    for prefix in ("uio", "ui", "uo"):
        if pad.startswith(prefix) and pad[len(prefix):].isdigit():
            return GROUP_BASE[prefix] + int(pad[len(prefix):])
    raise ValueError(f"not a pad: {pad}")


def pad_of(gpio: int) -> str:
    for prefix, base in sorted(GROUP_BASE.items(), key=lambda kv: -kv[1]):
        if gpio >= base:
            return f"{prefix}{gpio - base}"
    raise ValueError(gpio)

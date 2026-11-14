"""PIO arch-study hooks (ADR-0012). A PIO state machine has no attached units, so a
program's demand is its instruction slots, FIFOs and pads."""

from __future__ import annotations

from pioscope.image import ProgramImage


def units_used(image: ProgramImage) -> list[str]:
    return []


def bit_shares(image: ProgramImage, units: frozenset[str]) -> dict[str, float]:
    return {}


def fifos(image: ProgramImage) -> tuple[bool, bool]:
    """TX: a pull, or an out with autopull. RX: a push, or an in with autopush."""
    sm = image.config.get("sm") or {}
    names = {i.name for i in image.instructions}
    tx = "pull" in names or ("out" in names and bool(sm.get("autopull")))
    rx = "push" in names or ("in" in names and bool(sm.get("autopush")))
    return tx, rx


def bit_rate(image: ProgramImage) -> float | None:
    return None  # set by the clock divider and the loop length, not statically known

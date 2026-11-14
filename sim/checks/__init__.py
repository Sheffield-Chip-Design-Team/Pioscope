"""Backend-neutral checks over a Result: scoreboards and spec timing.

Every check raises CheckFailed with a message that says what the spec
requires, what was measured and where (cycle / time).
"""

from __future__ import annotations

from collections.abc import Sequence

from sim.bench.scenario import Result
from sim.decoders import I2cEvent, SpiWord, UartFrame


class CheckFailed(AssertionError):
    pass


def _us(result: Result, cycles: float) -> str:
    return f"{cycles / result.waveform.sysclk_hz * 1e6:.3f} us"


# ---- scoreboard -------------------------------------------------------------------


def expect_sequence(what: str, expected: Sequence[object], actual: Sequence[object]) -> None:
    if list(expected) != list(actual):
        n = next((i for i, (a, b) in enumerate(zip(expected, actual, strict=False)) if a != b),
                 min(len(expected), len(actual)))
        raise CheckFailed(
            f"{what}: first difference at index {n}\n"
            f"  expected ({len(expected)}): {list(expected)!r}\n"
            f"  actual   ({len(actual)}): {list(actual)!r}")


def no_conflicts(result: Result) -> None:
    if result.conflicts:
        raise CheckFailed(f"{len(result.conflicts)} bus conflicts, first: {result.conflicts[0]}")


def no_x(result: Result, nets: Sequence[str], after_cycle: int = 0) -> None:
    for n in nets:
        if result.waveform.has_x(n, after_cycle):
            raise CheckFailed(f"net '{n}' is X after cycle {after_cycle}")


# ---- UART ---------------------------------------------------------------------------


def uart_bit_period(result: Result, frames: Sequence[UartFrame], baud: float,
                    tol_ppm: float) -> None:
    """Measured bit period within tol_ppm of 1/baud for every frame."""
    nominal = result.waveform.sysclk_hz / baud
    for f in frames:
        if f.measured_bit_cycles is None:
            continue
        err = (f.measured_bit_cycles - nominal) / nominal * 1e6
        if abs(err) > tol_ppm:
            raise CheckFailed(
                f"UART bit period at cycle {f.start}: {f.measured_bit_cycles:.2f} cycles "
                f"({err:+.0f} ppm), spec 1/{baud:g} = {nominal:.2f} cycles +/-{tol_ppm:g} ppm")


# ---- SPI ----------------------------------------------------------------------------


def spi_timing(result: Result, words: Sequence[SpiWord], min_setup_cycles: int = 1,
               min_hold_cycles: int = 1) -> None:
    for w in words:
        if w.min_setup is not None and w.min_setup < min_setup_cycles:
            raise CheckFailed(f"SPI MOSI setup {w.min_setup} cycles < {min_setup_cycles} "
                              f"(word at cycle {w.start})")
        if w.min_hold is not None and w.min_hold < min_hold_cycles:
            raise CheckFailed(f"SPI MOSI hold {w.min_hold} cycles < {min_hold_cycles} "
                              f"(word at cycle {w.start})")


# ---- I2C ----------------------------------------------------------------------------

I2C_STANDARD = {"t_low_min_s": 4.7e-6, "t_high_min_s": 4.0e-6}
I2C_FAST = {"t_low_min_s": 1.3e-6, "t_high_min_s": 0.6e-6}


def i2c_timing(result: Result, events: Sequence[I2cEvent], spec: dict[str, float]) -> None:
    hz = result.waveform.sysclk_hz
    lo_min, hi_min = spec["t_low_min_s"] * hz, spec["t_high_min_s"] * hz
    for e in events:
        for t in e.t_low:
            if t < lo_min:
                raise CheckFailed(f"I2C tLOW {_us(result, t)} < {_us(result, lo_min)} "
                                  f"(byte at cycle {e.cycle})")
        for t in e.t_high:
            if t < hi_min:
                raise CheckFailed(f"I2C tHIGH {_us(result, t)} < {_us(result, hi_min)} "
                                  f"(byte at cycle {e.cycle})")

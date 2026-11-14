"""A device's own clock, asynchronous to the DUT's sysclk.

Tick n happens at ``offset_s + n * period_s + jitter(n)``. Jitter is per-edge
(non-cumulative), Gaussian, and a pure function of (seed, n), so the same
tick always lands at the same time: re-runs and backends stay identical.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceClock:
    freq_hz: float
    ppm: float = 0.0  # + = this clock runs fast
    offset_s: float = 0.0  # time of tick 0; sweep over [0, 1/sysclk) to vary phase
    jitter_rms_s: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        if self.freq_hz <= 0:
            raise ValueError("freq_hz must be > 0")
        if self.jitter_rms_s < 0:
            raise ValueError("jitter_rms_s must be >= 0")
        if self.jitter_rms_s * 6 >= self.period_s:
            raise ValueError("jitter_rms_s too large for this period (edges could reorder)")

    @property
    def period_s(self) -> float:
        return 1.0 / (self.freq_hz * (1 + self.ppm * 1e-6))

    def tick(self, n: float) -> float:
        """Time in seconds of tick `n`. Fractional n (e.g. 0.5) means a point between
        ideal ticks; jitter is applied only to integer ticks."""
        t = self.offset_s + n * self.period_s
        if self.jitter_rms_s and float(n).is_integer():
            j = random.Random(f"{self.seed}:{int(n)}").gauss(0.0, self.jitter_rms_s)
            # clamp to +/-3 sigma so edges never reorder (checked in __post_init__)
            lim = 3 * self.jitter_rms_s
            t += max(-lim, min(lim, j))
        return t

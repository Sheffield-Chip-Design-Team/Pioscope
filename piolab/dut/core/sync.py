"""Two-flop input synchroniser (CLAUDE.md §3): a pad level reaches the core two cycles later."""

from __future__ import annotations

from collections.abc import Sequence


class InputSync:
    STAGES = 2

    def __init__(self, width: int, reset: Sequence[int] | None = None) -> None:
        init = list(reset) if reset is not None else [1] * width
        self.stages: list[list[int]] = [list(init) for _ in range(self.STAGES)]

    def step(self, levels: Sequence[int]) -> list[int]:
        """Clock in this cycle's pad levels; return what the core sees now."""
        out = self.stages[-1]
        self.stages = [list(levels), *self.stages[:-1]]
        return out

    @property
    def seen(self) -> list[int]:
        return self.stages[-1]

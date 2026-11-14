"""Bounded FIFO between the host port and the core."""

from __future__ import annotations

from collections import deque


class Fifo:
    def __init__(self, depth: int) -> None:
        if depth < 0:
            raise ValueError("FIFO depth must be at least 0")
        self.depth = depth
        self.q: deque[int] = deque()

    def __len__(self) -> int:
        return len(self.q)

    @property
    def full(self) -> bool:
        return len(self.q) >= self.depth

    @property
    def empty(self) -> bool:
        return not self.q

    def push(self, word: int) -> bool:
        if self.full:
            return False
        self.q.append(word & 0xFFFF_FFFF)
        return True

    def pop(self) -> int | None:
        return self.q.popleft() if self.q else None

    def state(self) -> dict[str, object]:
        return {"level": len(self.q), "depth": self.depth, "words": list(self.q)}

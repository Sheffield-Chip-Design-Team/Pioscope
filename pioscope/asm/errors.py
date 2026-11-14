"""Assembler diagnostics: file, line, column, what was expected, and a hint."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    file: str
    line: int  # 1-based
    col: int  # 1-based
    end_col: int  # exclusive, 1-based
    source: str  # the full source line (no newline)

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.col}"


@dataclass
class AsmError(Exception):
    message: str
    span: Span | None = None
    expected: str | None = None
    hint: str | None = None
    severity: str = "error"  # error | warning

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def render(self) -> str:
        where = f"{self.span}: " if self.span else ""
        out = [f"{where}{self.severity}: {self.message}"]
        if self.span:
            gutter = f"{self.span.line:5d} | "
            out.append(gutter + self.span.source)
            width = max(1, self.span.end_col - self.span.col)
            out.append(" " * (len(gutter) - 2) + "| " + " " * (self.span.col - 1) + "^" * width)
        if self.expected:
            out.append(f"  expected: {self.expected}")
        if self.hint:
            out.append(f"  hint: {self.hint}")
        return "\n".join(out)

    def __str__(self) -> str:
        return self.render()


class AsmErrors(Exception):
    """One or more assembler errors (all collected, not just the first)."""

    def __init__(self, errors: list[AsmError]) -> None:
        self.errors = errors
        super().__init__(self.render())

    def render(self) -> str:
        n = len(self.errors)
        body = "\n\n".join(e.render() for e in self.errors)
        return f"{body}\n\n{n} error{'s' if n != 1 else ''}"

    def __str__(self) -> str:
        return self.render()


def did_you_mean(word: str, choices: Iterable[str]) -> str | None:
    m = difflib.get_close_matches(word, list(choices), n=1, cutoff=0.6)
    return f"did you mean '{m[0]}'?" if m else None

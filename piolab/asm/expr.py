"""Integer expressions in operands: `(N - 1)`, `[T3 - 1]`, `(1 << 4) | 2`.

A small recursive-descent evaluator over integer literals (piolab.asm.operands
`parse_number` syntax) with + - * / % << >> & | ^ ~ and parentheses. Identifiers have
already been replaced by their `.define` values; anything else is not an expression.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Callable

_OPS: dict[str, Callable[[int, int], int]] = {
    "|": operator.or_, "^": operator.xor, "&": operator.and_, "<<": operator.lshift,
    ">>": operator.rshift, "+": operator.add, "-": operator.sub, "*": operator.mul,
    "/": operator.floordiv, "%": operator.mod}
_TOKEN = re.compile(r"\s*(?:(<<|>>|[-+*/%&|^~()])|([0-9][\w.]*))")
_BINARY = [("|",), ("^",), ("&",), ("<<", ">>"), ("+", "-"), ("*", "/", "%")]


class _Parser:
    def __init__(self, text: str) -> None:
        self.toks: list[str] = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m or m.end() == pos:
                raise ValueError(text)
            self.toks.append(m.group(1) or m.group(2))
            pos = m.end()
            while pos < len(text) and text[pos].isspace():
                pos += 1
        self.i = 0

    def peek(self) -> str | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self) -> str:
        t = self.toks[self.i]
        self.i += 1
        return t

    def binary(self, level: int) -> int:
        if level == len(_BINARY):
            return self.unary()
        v = self.binary(level + 1)
        while self.peek() in _BINARY[level]:
            op = self.take()
            v = _OPS[op](v, self.binary(level + 1))
        return v

    def unary(self) -> int:
        t = self.peek()
        if t == "-":
            self.take()
            return -self.unary()
        if t == "~":
            self.take()
            return ~self.unary()
        if t == "(":
            self.take()
            v = self.binary(0)
            if self.peek() != ")":
                raise ValueError("missing )")
            self.take()
            return v
        from .operands import parse_number

        n = parse_number(self.take()) if t is not None else None
        if n is None:
            raise ValueError(t)
        return n


def evaluate(text: str) -> int | None:
    """The value of an integer expression, or None if `text` is not one."""
    try:
        p = _Parser(text)
        if not p.toks:
            return None
        v = p.binary(0)
        return v if p.peek() is None else None
    except (ValueError, IndexError, ZeroDivisionError):
        return None


_IDENT = re.compile(r"[A-Za-z_]\w*")


def expand(text: str, defines: dict[str, list[str]]) -> str:
    """Replace every identifier in `text` that is a single-item .define."""
    return _IDENT.sub(lambda m: defines[m.group(0)][0]
                      if len(defines.get(m.group(0), ())) == 1 else m.group(0), text)

"""Line-oriented lexer.

Each source line becomes at most one :class:`Statement`::

    [label:] [mnemonic|.directive] item (sep item)* [side <item>] ['[' delay ']']   ; comment

Items are separated by commas and/or whitespace; a parenthesised expression is one
item. An item is kept as raw text
(e.g. ``x--``, ``osr>pins``, ``dur=3``, ``~y``) and typed later against
the ISA pack's yaml, so the lexer knows nothing about particular instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import AsmError, Span

_LABEL = re.compile(r"\s*([A-Za-z_]\w*)\s*:(?!:)")
_HEAD = re.compile(r"\s*(\.?[A-Za-z_][\w.]*)")


@dataclass(frozen=True)
class Item:
    text: str
    span: Span


@dataclass
class Statement:
    line: int
    source: str
    label: Item | None = None
    head: Item | None = None
    items: list[Item] = field(default_factory=list)
    side: Item | None = None
    delay: Item | None = None

    @property
    def is_directive(self) -> bool:
        return self.head is not None and self.head.text.startswith(".")


def strip_comment(line: str) -> str:
    cut = len(line)
    for marker in (";", "//"):
        i = line.find(marker)
        if i != -1:
            cut = min(cut, i)
    return line[:cut]


def _span(file: str, lineno: int, source: str, start: int, end: int) -> Span:
    return Span(file, lineno, start + 1, end + 1, source)


def lex(source: str, file: str = "<input>") -> tuple[list[Statement], list[AsmError]]:
    stmts: list[Statement] = []
    errors: list[AsmError] = []
    for lineno, raw in enumerate(source.splitlines(), start=1):
        src = raw.rstrip("\n").expandtabs(8)
        code = strip_comment(src)
        if not code.strip():
            continue
        st = Statement(line=lineno, source=src)
        pos = 0
        m = _LABEL.match(code, pos)
        if m:
            st.label = Item(m.group(1), _span(file, lineno, src, m.start(1), m.end(1)))
            pos = m.end()
        m = _HEAD.match(code, pos)
        if m:
            st.head = Item(m.group(1).lower(), _span(file, lineno, src, m.start(1), m.end(1)))
            pos = m.end()
        elif code[pos:].strip():
            i = len(code) - len(code[pos:].lstrip())
            errors.append(
                AsmError(
                    f"unexpected '{code[i]}'",
                    _span(file, lineno, src, i, i + 1),
                    expected="a label, an instruction or a directive",
                )
            )
            continue
        pieces = _split(code, pos, file, lineno, src, errors)
        pieces = _merge_kw(pieces, file, lineno, src)
        # Pull out modifiers: `side <v>` and `[delay]`.
        rest: list[Item] = []
        i = 0
        while i < len(pieces):
            p = pieces[i]
            if p.text.startswith("["):
                if st.delay is not None:
                    errors.append(AsmError("more than one [delay]", p.span))
                st.delay = Item(p.text[1:-1].strip(), p.span)
            elif p.text.lower() == "side" and not st.is_directive:
                if i + 1 >= len(pieces):
                    errors.append(AsmError("'side' needs a value", p.span, expected="side <value>"))
                else:
                    st.side = Item(pieces[i + 1].text, pieces[i + 1].span)
                    i += 1
            else:
                rest.append(p)
            i += 1
        st.items = rest
        stmts.append(st)
    return stmts, errors


def _split(
    code: str, pos: int, file: str, lineno: int, src: str, errors: list[AsmError]
) -> list[Item]:
    out: list[Item] = []
    i, n = pos, len(code)
    while i < n:
        c = code[i]
        if c.isspace() or c == ",":
            i += 1
            continue
        start = i
        if c == "[":
            j = code.find("]", i)
            if j == -1:
                errors.append(
                    AsmError(
                        "unterminated '['",
                        _span(file, lineno, src, i, n),
                        expected="[delay]",
                        hint="close the delay with ']'",
                    )
                )
                return out
            i = j + 1
        elif c == "(":  # an expression: one item up to the matching ')'
            depth = 0
            while i < n:
                depth += {"(": 1, ")": -1}.get(code[i], 0)
                i += 1
                if depth == 0:
                    break
            if depth:
                errors.append(AsmError("unterminated '('", _span(file, lineno, src, start, n),
                                       expected="(expression)", hint="close it with ')'"))
                return out
        else:
            while i < n and not code[i].isspace() and code[i] not in ",[":
                i += 1
        out.append(Item(code[start:i], _span(file, lineno, src, start, i)))
    return out


def _merge_kw(pieces: list[Item], file: str, lineno: int, src: str) -> list[Item]:
    """Join `key = value` / `key= value` / `key =value` into one `key=value` item."""
    out: list[Item] = []
    i = 0
    while i < len(pieces):
        cur = pieces[i]
        while i + 1 < len(pieces):
            nxt = pieces[i + 1]
            joinable = (cur.text.endswith("=") and not cur.text.endswith("!=")) or (
                nxt.text.startswith("=") and re.fullmatch(r"[A-Za-z_]\w*", cur.text)
            )
            if not joinable:
                break
            sp = _span(file, lineno, src, cur.span.col - 1, nxt.span.end_col - 1)
            cur = Item(cur.text + nxt.text, sp)
            i += 1
        out.append(cur)
        i += 1
    return out

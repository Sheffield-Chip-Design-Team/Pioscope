"""Type operand items against the ISA yaml's operand types and bind them to operand slots.

Nothing here names a particular instruction: every rule is driven by the
operand types and operand specs in the ISA.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pioscope.isa import Isa, OperandSpec, OperandType

from .errors import AsmError, Span, did_you_mean
from .expr import evaluate
from .lexer import Item

_IDENT = re.compile(r"[A-Za-z_]\w*")
_NUM = re.compile(r"(?i)^(0x[0-9a-f_]+|0b[01_]+|\d[\d_]*(?:\.\d+)?)([kM])?$")


class _NoMatch:
    pass


NO_MATCH: Any = _NoMatch()


def parse_number(text: str) -> int | None:
    """Parse 42, 0x2a, 0b101, 25M, 1.5k. Returns None if not a number or not integral."""
    m = _NUM.match(text)
    if not m:
        return None
    body, suffix = m.group(1).replace("_", ""), m.group(2)
    low = body.lower()
    if low.startswith("0x"):
        val: float = int(low, 16)
    elif low.startswith("0b"):
        val = int(low[2:], 2)
    else:
        val = float(body) if "." in body else int(body)
    if suffix:
        val *= {"k": 1_000, "m": 1_000_000}[suffix.lower()]
    if val != int(val):
        return None
    return int(val)


def match(t: OperandType, item: Item, isa: Isa) -> Any:
    """Return the typed value of `item`, or NO_MATCH. Raise AsmError for a
    value that is clearly of this type but invalid (e.g. out of range)."""
    text = item.text
    k = t.kind
    if k == "int":
        v = parse_number(text)
        if v is None and text.startswith("("):
            v = evaluate(text)
        if v is None:
            return NO_MATCH
        assert t.min is not None and t.max is not None
        if not t.min <= v <= t.max:
            raise AsmError(
                f"value {v} out of range", item.span, expected=t.describe(), hint=None
            )
        return v
    if k == "enum":
        low = text.lower()
        return low if low in t.values else NO_MATCH
    if k == "flag":
        return True if text.lower() == t.word else NO_MATCH
    if k == "label":  # a label, or an absolute address (checked in pass 2)
        n = parse_number(text)
        if n is not None:
            return n
        return text if _IDENT.fullmatch(text) else NO_MATCH
    if k in ("ref", "word"):
        return text if _IDENT.fullmatch(text) else NO_MATCH
    if k == "pattern":
        assert t.regex is not None
        m = t.regex.fullmatch(text)
        if not m:
            return NO_MATCH
        out: dict[str, Any] = {}
        for g, raw in m.groupdict().items():
            if g in t.ints:
                out[g] = parse_number(raw) if raw is not None else None
                if raw is not None and out[g] is None:
                    raise AsmError(f"'{raw}' is not an integer", item.span)
            elif g in t.fields:
                allowed = t.fields[g]
                out[g] = raw if raw is not None else (allowed[0] if allowed else None)
            else:
                out[g] = raw
        return out
    if k == "cond":
        low = text.lower()
        return low if low in isa.conditions else NO_MATCH
    raise AsmError(f"internal: operand kind '{k}' cannot be matched", item.span)


def syntax(head: str, specs: Sequence[OperandSpec]) -> str:
    parts = []
    for s in specs:
        p = f"{s.key}=<{s.type.name}>" if s.key else s.name
        if s.type.kind == "rest":
            p += " ..."
        parts.append(f"[{p}]" if s.optional else p)
    return f"{head} " + ", ".join(parts) if parts else head


def bind(
    head: Item, specs: Sequence[OperandSpec], items: list[Item], isa: Isa
) -> dict[str, Any]:
    """Bind items to operand specs. Raises AsmError with the first problem."""
    usage = syntax(head.text, specs)
    by_key = {s.key: s for s in specs if s.key}
    values: dict[str, Any] = {}
    positional: list[Item] = []

    for it in items:
        if "=" in it.text:
            k, _, v = it.text.partition("=")
            if _IDENT.fullmatch(k):
                spec = by_key.get(k.lower())
                if spec is None and any(s.type.kind == "rest" for s in specs):
                    positional.append(it)  # free-form items (e.g. `.pins tx=uo0`)
                    continue
                if spec is None:
                    raise AsmError(
                        f"'{head.text}' has no option '{k}'",
                        it.span,
                        expected="one of " + ", ".join(f"{x}=" for x in by_key) if by_key else
                        "no keyword options",
                        hint=did_you_mean(k, by_key) or f"syntax: {usage}",
                    )
                if spec.name in values:
                    raise AsmError(f"option '{k}' given twice", it.span)
                vitem = Item(v, Span(it.span.file, it.span.line, it.span.col + len(k) + 1,
                                     it.span.end_col, it.span.source))
                if not v:
                    raise AsmError(f"option '{k}=' needs a value", it.span,
                                   expected=spec.type.describe())
                val = match(spec.type, vitem, isa)
                if val is NO_MATCH:
                    raise AsmError(
                        f"bad value '{v}' for {k}=",
                        vitem.span,
                        expected=spec.type.describe(),
                        hint=_enum_hint(spec.type, v),
                    )
                values[spec.name] = val
                continue
        positional.append(it)

    i = 0
    for spec in specs:
        if spec.key:
            if spec.name not in values:
                if not spec.optional:
                    raise AsmError(
                        f"'{head.text}' needs {spec.key}=",
                        head.span,
                        expected=f"{spec.key}=<{spec.type.describe()}>",
                        hint=f"syntax: {usage}",
                    )
                values[spec.name] = spec.default
            continue
        if spec.type.kind == "rest":
            values[spec.name] = [p.text for p in positional[i:]]
            i = len(positional)
            continue
        if i < len(positional):
            it = positional[i]
            val = match(spec.type, it, isa)
            if val is not NO_MATCH:
                i += 1
                if spec.type.kind == "cond":
                    cond = isa.conditions[val]
                    if cond.arg is not None:
                        if i >= len(positional):
                            raise AsmError(f"condition '{val}' needs an argument", it.span,
                                           expected=cond.arg.describe())
                        arg = match(cond.arg, positional[i], isa)
                        if arg is NO_MATCH:
                            raise AsmError(f"bad argument for '{val}'", positional[i].span,
                                           expected=cond.arg.describe())
                        val = f"{val} {arg}"
                        i += 1
                values[spec.name] = val
                continue
            if not spec.optional:
                raise AsmError(
                    f"bad {spec.name} '{it.text}' for '{head.text}'",
                    it.span,
                    expected=f"{spec.name}: {spec.type.describe()}",
                    hint=_enum_hint(spec.type, it.text) or f"syntax: {usage}",
                )
        elif not spec.optional:
            end = items[-1].span if items else head.span
            raise AsmError(
                f"'{head.text}' is missing operand '{spec.name}'",
                Span(end.file, end.line, end.end_col, end.end_col + 1, end.source),
                expected=f"{spec.name}: {spec.type.describe()}",
                hint=f"syntax: {usage}",
            )
        values[spec.name] = spec.default

    if i < len(positional):
        extra = positional[i]
        raise AsmError(
            f"unexpected operand '{extra.text}' for '{head.text}'",
            extra.span,
            hint=_extra_hint(specs, extra, isa) or f"syntax: {usage}",
        )
    return values


def _enum_hint(t: OperandType, text: str) -> str | None:
    if t.kind == "enum":
        return did_you_mean(text.lower(), t.values)
    return None


def _extra_hint(specs: Sequence[OperandSpec], extra: Item, isa: Isa) -> str | None:
    # An unexpected item may be a mistyped optional operand or condition.
    words: list[str] = []
    for s in specs:
        if s.type.kind == "enum":
            words += s.type.values
        elif s.type.kind == "flag" and s.type.word:
            words.append(s.type.word)
        elif s.type.kind == "cond":
            words += list(isa.conditions)
    return did_you_mean(extra.text.lower(), words)

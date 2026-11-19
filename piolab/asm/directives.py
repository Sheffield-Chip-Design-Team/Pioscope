"""Directive handlers: directives become config, never instruction slots.

GENERIC holds the handlers every ISA pack can use. A pack adds its own (or overrides
one) in the module its yaml names under `hooks: directives:`, which defines a
`DIRECTIVE_HANDLERS` dict built with `registry()`. Every directive in a yaml must have
exactly one handler in `handlers(isa)` (enforced by the coverage tests).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from piolab.isa import Isa

from .errors import AsmError, did_you_mean
from .lexer import Item, Statement


@dataclass
class AsmContext:
    isa: Isa
    file: str
    name: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    defines: dict[str, list[str]] = field(default_factory=dict)
    addr: int = 0
    wrap_target: int | None = None
    wrap: int | None = None
    errors: list[AsmError] = field(default_factory=list)


Handler = Callable[[AsmContext, Statement, dict[str, Any], dict[str, str]], None]
Decorator = Callable[[str], Callable[[Handler], Handler]]


def registry() -> tuple[dict[str, Handler], Decorator]:
    """A handler table and the decorator that fills it (`@directive(".name")`)."""
    table: dict[str, Handler] = {}

    def directive(name: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            table[name] = fn
            return fn

        return deco

    return table, directive


GENERIC, directive = registry()


def handlers(isa: Isa) -> dict[str, Handler]:
    """The handlers for `isa`: GENERIC plus (overridden by) the pack's own."""
    hook = isa.hook("directives")
    extra: dict[str, Handler] = getattr(hook, "DIRECTIVE_HANDLERS", {}) if hook else {}
    return {**GENERIC, **extra}


def head(st: Statement) -> Item:
    assert st.head is not None
    return st.head


@directive(".program")
def _program(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    if ctx.name is not None:
        raise AsmError(
            "only one .program per source file",
            head(st).span,
            hint="put each program in its own source file",
        )
    ctx.name = v["name"]


_ROLE_MAP = re.compile(r"^(?P<role>[A-Za-z_]\w*)=(?P<target>.+)$")


@directive(".pins")
def _pins(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    pin_t = ctx.isa.types["pin_map"]
    assert pin_t.regex is not None
    pins = ctx.config.setdefault("pins", {})
    used = {p["pad"]: r for r, p in pins.items()}
    if not st.items:
        raise AsmError(".pins needs at least one role=pad", head(st).span,
                       expected="role=pad[/mode], e.g. tx=uo0 or sda=uio1/open_drain")
    for it in st.items:
        m = _ROLE_MAP.match(it.text)
        if not m:
            raise AsmError(f"bad pin mapping '{it.text}'", it.span,
                           expected="role=pad[/mode], e.g. tx=uo0 or sda=uio1/open_drain")
        role, target = m.group("role"), m.group("target")
        pm = pin_t.regex.fullmatch(target)
        if not pm:
            pads = ctx.isa.pad_names()
            raise AsmError(
                f"unknown pad '{target}' for role '{role}'",
                it.span,
                expected=f"one of {pads[0]}..{pads[-1]} optionally followed by "
                + "/" + "|/".join(ctx.isa.pin_modes),
                hint=did_you_mean(target.split("/")[0], pads),
            )
        pad, mode = pm.group("pad"), pm.group("mode") or "push_pull"
        pdir = ctx.isa.pad_dir(pad)
        if mode == "open_drain" and pdir != "bidir":
            raise AsmError(
                f"pad '{pad}' cannot be open-drain",
                it.span,
                hint="open-drain needs an output enable; use a uio pad",
            )
        if role in pins:
            raise AsmError(f"role '{role}' mapped twice", it.span)
        if pad in used:
            raise AsmError(f"pad '{pad}' already used by role '{used[pad]}'", it.span)
        pins[role] = {"pad": pad, "mode": mode, "dir": pdir}
        used[pad] = role


@directive(".side_set")
def _side_set(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    width = ctx.isa.encoding.width("side")
    need = v["count"] + (1 if v["opt"] else 0)
    if need > width:
        raise AsmError(
            f".side_set {v['count']}{' opt' if v['opt'] else ''} needs {need} bits; "
            f"the side field is {width}",
            head(st).span,
            hint="reduce the side-set width (draft encoding limit)",
        )
    ctx.config["side_set"] = {
        "count": v["count"], "opt": v["opt"], "pindirs": v["pindirs"], "base": v["base"],
    }


@directive(".fifo")
def _fifo(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    ctx.config["fifo"] = {"tx": v["tx"], "rx": v["rx"]}


@directive(".clkdiv")
def _clkdiv(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    whole, frac = v["div"]["int"], v["div"]["frac"]
    span = st.items[0].span if st.items else head(st).span
    if not 1 <= whole <= 0xFFFF:
        raise AsmError(f"clock divider {whole} out of range", span,
                       expected="an integer part of 1..65535", hint="1 means every sysclk")
    steps = round(float(f"0.{frac}") * 256) if frac else 0
    if steps == 256:  # e.g. .999 rounds up to the next whole divider
        whole, steps = whole + 1, 0
    ctx.config["clkdiv"] = {"int": whole, "frac": steps}


@directive(".wrap_target")
def _wrap_target(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    if ctx.wrap_target is not None:
        raise AsmError("duplicate .wrap_target", head(st).span)
    ctx.wrap_target = ctx.addr


@directive(".wrap")
def _wrap(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    if ctx.wrap is not None:
        raise AsmError("duplicate .wrap", head(st).span)
    if ctx.addr == 0:
        raise AsmError(".wrap before any instruction", head(st).span,
                       hint=".wrap goes after the last instruction of the loop")
    ctx.wrap = ctx.addr - 1


@directive(".define")
def _define(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    if v["name"] in ctx.defines:
        raise AsmError(f"'{v['name']}' already defined", head(st).span)
    if not v["value"]:
        raise AsmError(f".define {v['name']} needs a value", head(st).span)
    ctx.defines[v["name"]] = v["value"]

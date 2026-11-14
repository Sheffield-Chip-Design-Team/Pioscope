"""PIO pack directives (ADR-0012): `.side_set` sized for the 5-bit delay/side-set field,
and `.sm_config`, the SM configuration the Pico SDK would set from C.

`.sm_config` pins name `.pins` roles or GPIO numbers (ui0-7 = 0-7, uo0-7 = 8-15,
uio0-7 = 16-23); they are resolved to GPIO numbers in `config["sm"]` after pass 1, so
the directive may come before or after `.pins`.
"""

from __future__ import annotations

from typing import Any

from sim.asm.directives import AsmContext, head, registry
from sim.asm.errors import AsmError, did_you_mean
from sim.asm.lexer import Statement

from .pins import NUM_GPIO, gpio_of

DIRECTIVE_HANDLERS, directive = registry()

FIELD_BITS = 5


@directive(".side_set")
def _side_set(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    need = v["count"] + (1 if v["opt"] else 0)
    if need > FIELD_BITS:
        raise AsmError(
            f".side_set {v['count']}{' opt' if v['opt'] else ''} needs {need} bits; the "
            f"delay/side-set field is {FIELD_BITS}", head(st).span,
            hint="at most 5 side-set bits, or 4 with opt")
    if ctx.addr:
        raise AsmError(".side_set after the first instruction", head(st).span,
                       hint="put .side_set before the program body")
    ctx.config["side_set"] = {"count": v["count"], "opt": v["opt"], "pindirs": v["pindirs"],
                              "base": None}


@directive(".sm_config")
def _sm_config(ctx: AsmContext, st: Statement, v: dict[str, Any], _g: dict[str, str]) -> None:
    if "sm_raw" in ctx.config:
        raise AsmError("duplicate .sm_config", head(st).span,
                       hint="give every option in one .sm_config line")
    for a, b in (("autopull", "pull_thresh"), ("autopush", "push_thresh")):
        if v[a] is not None and v[b] is not None:
            raise AsmError(f"give {a}= or {b}=, not both", head(st).span,
                           hint=f"{a}=N turns {a} on with threshold N")
    ctx.config["sm_raw"] = dict(v)


def _resolve_pin(ctx: AsmContext, ref: str, what: str) -> int:
    pins = ctx.config.get("pins", {})
    if ref.isdigit():
        n = int(ref)
        if n >= NUM_GPIO:
            raise AsmError(f"{what}: GPIO {n} does not exist (0..{NUM_GPIO - 1})", None)
        return n
    if ref not in pins:
        raise AsmError(f".sm_config {what}={ref}: no such role in .pins", None,
                       hint=did_you_mean(ref, pins) or "map it with `.pins name=pad`")
    return gpio_of(pins[ref]["pad"])


def resolve_config(ctx: AsmContext, stmts: list[Statement]) -> None:
    """Build config["sm"] (GPIO numbers, thresholds) from .sm_config and .pins."""
    raw = ctx.config.pop("sm_raw", None) or {}
    st = next((s for s in stmts if s.head and s.head.text == ".sm_config"), None)
    pins = ctx.config.get("pins", {})
    outs = [gpio_of(p["pad"]) for p in pins.values() if p["dir"] in ("out", "bidir")]
    ins = [gpio_of(p["pad"]) for p in pins.values() if p["dir"] in ("in", "bidir")]
    first_out = outs[0] if outs else 0
    first_in = ins[0] if ins else 0
    sm: dict[str, Any] = {}
    try:
        for key, default_count in (("out", 1), ("set", 1)):
            r = raw.get(key)
            base = _resolve_pin(ctx, r["pin"], key) if r else first_out
            count = r["count"] if r and r["count"] is not None else default_count
            limit = 32 if key == "out" else 5
            if not 0 <= count <= limit:
                raise AsmError(f".sm_config {key}= count {count} is not 0..{limit}", None)
            sm[key] = {"base": base, "count": count}
        for key, default in (("in", first_in), ("sideset", first_out), ("jmp", first_in)):
            r = raw.get(key)
            sm[key] = _resolve_pin(ctx, r["pin"], key) if r else default
    except AsmError as e:
        ctx.errors.append(AsmError(e.message, st.head.span if st and st.head else None,
                                   expected=e.expected, hint=e.hint))
        return
    sm["out_shift"] = raw.get("out_shift") or "right"
    sm["in_shift"] = raw.get("in_shift") or "right"
    sm["autopull"] = raw.get("autopull") is not None
    sm["pull_thresh"] = raw.get("autopull") or raw.get("pull_thresh") or 32
    sm["autopush"] = raw.get("autopush") is not None
    sm["push_thresh"] = raw.get("autopush") or raw.get("push_thresh") or 32
    high = raw.get("high")
    try:
        sm["high"] = [_resolve_pin(ctx, p, "high") for p in high["pins"].split("|")] if high \
            else []
    except AsmError as e:
        ctx.errors.append(AsmError(e.message, st.head.span if st and st.head else None,
                                   hint=e.hint))
        return
    sm["fifo"] = raw.get("fifo") or "none"
    status = raw.get("status")
    sm["status"] = ({"fifo": status["fifo"], "n": status["n"]} if status
                    else {"fifo": "tx", "n": 0})
    ctx.config["sm"] = sm

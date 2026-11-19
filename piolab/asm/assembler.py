"""Assembler driver: source -> ProgramImage (IR + draft words + config + symbols)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from piolab.image import IrInstr, ProgramImage
from piolab.isa import Isa, default_isa

from . import directives as _d
from .encode import encoder_for
from .errors import AsmError, AsmErrors, did_you_mean
from .expr import evaluate, expand
from .lexer import Item, Statement, lex, strip_comment
from .operands import bind, parse_number


def _substitute(items: list[Item], defines: dict[str, list[str]]) -> list[Item]:
    out: list[Item] = []
    for it in items:
        if it.text.startswith("("):  # an expression: expand the defines inside it
            out.append(Item(expand(it.text, defines), it.span))
            continue
        if it.text in defines:
            out += [Item(t, it.span) for t in defines[it.text]]
            continue
        k, eq, v = it.text.partition("=")
        if eq and v in defines and len(defines[v]) == 1:
            out.append(Item(f"{k}={defines[v][0]}", it.span))
            continue
        out.append(it)
    return out


def assemble(source: str, file: str = "<input>", isa: Isa | None = None) -> ProgramImage:
    isa = isa or default_isa()
    stmts, errors = lex(source, file)
    ctx = _d.AsmContext(isa=isa, file=file, errors=errors)
    instrs: list[tuple[IrInstr, Statement]] = []
    labels: dict[str, tuple[int, Item]] = {}

    # ---- pass 1: directives, labels, operand binding -----------------------
    for st in stmts:
        if st.label is not None:
            name = st.label.text
            if name in labels:
                ctx.errors.append(AsmError(
                    f"label '{name}' already defined", st.label.span,
                    hint=f"first defined at {file}:{labels[name][1].span.line}"))
            else:
                labels[name] = (ctx.addr, st.label)
        if st.head is None:
            continue
        try:
            if st.is_directive:
                _directive(ctx, st)
            else:
                ins = _instruction(ctx, st)
                instrs.append((ins, st))
                ctx.addr += 1
        except AsmError as e:
            ctx.errors.append(e)

    if ctx.name is None and stmts:
        ctx.errors.append(AsmError(
            "missing .program", None, hint="start the file with `.program <name>`"))

    # ---- pass 2: resolve labels and config references -----------------------
    for ins, st in instrs:
        try:
            _resolve(ctx, ins, st, labels)
        except AsmError as e:
            ctx.errors.append(e)
    _resolve_config(ctx, stmts)

    imem = int(isa.params["imem_size"])
    if ctx.addr > imem:
        first_over = instrs[imem][1].head
        ctx.errors.append(AsmError(
            f"program '{ctx.name}' needs {ctx.addr} instruction slots; imem_size is {imem}",
            first_over.span if first_over is not None else None,
            hint="this is a metric worth recording; raise params.imem_size to experiment"))

    if ctx.errors:
        raise AsmErrors(ctx.errors)

    if ctx.addr:
        ctx.config["wrap"] = {
            "target": ctx.wrap_target if ctx.wrap_target is not None else 0,
            "wrap": ctx.wrap if ctx.wrap is not None else ctx.addr - 1,
        }

    image = ProgramImage(
        isa_version=isa.version,
        name=ctx.name or "",
        imem_size=imem,
        instructions=[i for i, _ in instrs],
        words=[],
        config=ctx.config,
        symbols={k: a for k, (a, _) in labels.items()},
        source=file,
        isa_pack=isa.pack,
        word_bits=isa.encoding.word_bits,
    )

    words, problems = encoder_for(isa).encode_image(image, isa)

    if problems and isa.encoding.strict:
        raise AsmErrors([AsmError(f"encoding: {p}") for p in problems])

    image.words = words
    image.warnings = [f"encoding (draft, not strict): {p}" for p in problems]
    return image


def assemble_file(path: Path | str, isa: Isa | None = None) -> ProgramImage:
    """Assemble a source file. Without `isa`, the pack whose source suffix the file has
    (`.pio`, ...) decides; an unknown suffix uses the active pack."""
    p = Path(path)
    if isa is None:
        from piolab.isa import load_isa
        from piolab.packs import for_path

        pack = for_path(p)
        isa = load_isa(pack.spec) if pack is not None else None
    return assemble(p.read_text(), str(p), isa)


def _directive(ctx: _d.AsmContext, st: Statement) -> None:
    assert st.head is not None
    found = ctx.isa.find_directive(st.head.text)
    if found is None:
        raise AsmError(
            f"unknown directive '{st.head.text}'",
            st.head.span,
            expected="a directive: " + ", ".join(ctx.isa.directives),
            hint=did_you_mean(st.head.text, ctx.isa.directives),
        )
    spec, groups = found
    if spec.name != ".program" and ctx.name is None:
        raise AsmError(f"'{st.head.text}' before .program", st.head.span,
                       hint="start the file with `.program <name>`")
    items = st.items if spec.name == ".define" else _substitute(st.items, ctx.defines)
    values = bind(st.head, spec.operands, items, ctx.isa)
    handler = _d.handlers(ctx.isa).get(spec.name)
    if handler is None:
        raise AsmError(f"internal: no handler for directive {spec.name}", st.head.span)
    st.items = items
    handler(ctx, st, values, groups)


def _instruction(ctx: _d.AsmContext, st: Statement) -> IrInstr:
    assert st.head is not None
    head = st.head
    spec = ctx.isa.instructions.get(head.text)
    if spec is None:
        raise AsmError(
            f"unknown instruction '{head.text}'",
            head.span,
            expected="an instruction or directive",
            hint=did_you_mean(head.text, list(ctx.isa.instructions) + list(ctx.isa.directives)),
        )
    if ctx.name is None:
        raise AsmError(f"'{head.text}' before .program", head.span,
                       hint="start the file with `.program <name>`")
    values = bind(head, spec.operands, _substitute(st.items, ctx.defines), ctx.isa)

    side: int | None = None
    delay = 0
    if st.side is not None:
        if "side" not in spec.modifiers:
            raise AsmError(f"'{head.text}' does not take a side-set modifier", st.side.span,
                           hint="this instruction's spec has no side modifier")
        sv = _int(st.side.text, ctx.defines)
        ss = ctx.config.get("side_set")
        if ss is None:
            raise AsmError("side-set used without .side_set", st.side.span,
                           hint="declare `.side_set <n> [opt]` before the program body")
        if sv is None or not 0 <= sv < (1 << ss["count"]):
            raise AsmError(f"bad side-set value '{st.side.text}'", st.side.span,
                           expected=f"an integer 0..{(1 << ss['count']) - 1} "
                           f"(.side_set {ss['count']})")
        side = sv
    elif "side" in spec.modifiers:
        ss = ctx.config.get("side_set")
        if ss is not None and ss["count"] and not ss["opt"]:
            raise AsmError(f"'{head.text}' needs a side-set value", head.span,
                           hint="side-set is mandatory unless declared `.side_set <n> opt`")
    if st.delay is not None:
        if "delay" not in spec.modifiers:
            raise AsmError(f"'{head.text}' does not take a [delay]", st.delay.span,
                           hint="this instruction's spec has no delay modifier")
        dv = _int(st.delay.text, ctx.defines)
        dmax_fn = getattr(encoder_for(ctx.isa), "delay_max", None)
        dmax = int(dmax_fn(ctx.isa, ctx.config) if dmax_fn else ctx.isa.params["delay_max"])
        if dv is None or not 0 <= dv <= dmax:
            raise AsmError(f"bad delay '[{st.delay.text}]'", st.delay.span,
                           expected=f"an integer 0..{dmax}",
                           hint="split long waits into a loop" if dv and dv > dmax else None)
        delay = dv
    return IrInstr(
        addr=ctx.addr,
        name=spec.name,
        operands=values,
        side=side,
        delay=delay,
        src=f"{ctx.file}:{st.line}",
        text=_code_text(st),
    )


def _int(text: str, defines: dict[str, list[str]]) -> int | None:
    """A side-set or delay value: a number, a .define, or an expression of them."""
    t = expand(text, defines)
    v = parse_number(t)
    return v if v is not None else evaluate(t)


def _code_text(st: Statement) -> str:
    code = strip_comment(st.source)
    if st.label is not None:
        code = code[st.label.span.end_col - 1 :].lstrip().removeprefix(":")
    return " ".join(code.split())


def _resolve(
    ctx: _d.AsmContext, ins: IrInstr, st: Statement, labels: dict[str, tuple[int, Item]]
) -> None:
    spec = ctx.isa.instructions[ins.name]
    by_text = {it.text: it for it in st.items}
    for o in spec.operands:
        v = ins.operands[o.name]
        k = o.type.kind
        if k == "label" and isinstance(v, int):
            imem = int(ctx.isa.params["imem_size"])
            if not 0 <= v < imem:
                it = by_text.get(str(v))
                raise AsmError(f"address {v} is outside instruction memory", it.span if it
                               else None, expected=f"an address 0..{imem - 1} or a label")
        elif k == "label":
            if v not in labels:
                it = by_text.get(v)
                raise AsmError(f"undefined label '{v}'", it.span if it else None,
                               hint=did_you_mean(v, labels) or "define it with `name:`")
            ins.operands[o.name] = labels[v][0]
        elif k == "ref" and v is not None:
            assert o.type.table is not None
            known = list(ctx.config.get(o.type.table, {}))
            known += list(ctx.isa.builtin_refs.get(o.type.table, ()))
            if v not in known:
                it = by_text.get(v)
                raise AsmError(
                    f"unknown {o.type.table} config '{v}'", it.span if it else None,
                    hint=did_you_mean(v, known) or f"define it with a directive first "
                    f"(known: {', '.join(known) or 'none'})")


def _resolve_config(ctx: _d.AsmContext, stmts: list[Statement]) -> None:
    cfg = ctx.config
    hook = ctx.isa.hook("directives")
    if hook is not None and hasattr(hook, "resolve_config"):
        hook.resolve_config(ctx, stmts)  # pack checks across directives (piolab.packs)
    ss = cfg.get("side_set")
    if ss and ss.get("base") and ss["base"] not in cfg.get("pins", {}):
        st = next((s for s in stmts if s.head and s.head.text == ".side_set"), None)
        ctx.errors.append(AsmError(
            f".side_set base '{ss['base']}' is not a role in .pins",
            st.head.span if st and st.head else None,
            hint=did_you_mean(ss["base"], cfg.get("pins", {}))))


def config_summary(config: dict[str, Any]) -> list[str]:
    lines = []
    for k, v in config.items():
        lines.append(f"{k}: {v}")
    return lines

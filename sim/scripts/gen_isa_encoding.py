"""Generate <pack docs>/isa-encoding.adoc: the field-by-field instruction encoding.

RISC-V-spec style: per instruction an ASCII field diagram (readable anywhere)
plus a WaveDrom `reg` block (rendered by asciidoctor-diagram / Kroki). Every
layout comes from the ISA's encoder module (`instr_layout`, `field_usage`; the
generic field encoder in sim.asm.encode, or the pack's `hooks: encoder:`), so the
document cannot disagree with the words the assembler emits. Field roles, notes and
tier headings come from the yaml.

    uv run pioscope isa-docs
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sim.asm import assemble_file
from sim.asm.encode import Slot, _clog2, encoder_for, type_bits
from sim.asm.operands import syntax
from sim.image import IrInstr, ProgramImage
from sim.isa import InstrSpec, Isa, OperandType, default_isa
from sim.packs import active


def out_path() -> Path:
    return active().docs / "isa-encoding.adoc"


def field_index(isa: Isa, name: str) -> int:
    """Position of an encoding field (MSB first): picks its colour in the diagrams."""
    return next((i for i, (n, _) in enumerate(isa.encoding.fields) if n == name), 0)


def structural(isa: Isa) -> set[str]:
    """Fields no operand is packed into (opcode, delay, side-set): full by construction."""
    used = {o.field for s in isa.instructions.values() for o in s.operands}
    return {n for n, _ in isa.encoding.fields if n not in used or n == "opcode"}


def by_opcode(isa: Isa) -> dict[int, list[InstrSpec]]:
    out: dict[int, list[InstrSpec]] = {}
    for s in isa.instructions.values():
        out.setdefault(s.opcode, []).append(s)
    return out


def tier_groups(isa: Isa) -> list[tuple[int | None, str, list[InstrSpec]]]:
    """(tier, heading, instructions): by the yaml's `tiers:`, else one group."""
    if not isa.tiers:
        return [(None, "Instructions", list(isa.instructions.values()))]
    return [(t, f"Tier {t}: {title}", [s for s in isa.instructions.values() if s.tier == t])
            for t, title in sorted(isa.tiers.items())]


def enum_codes(t: OperandType, group: str | None = None) -> list[tuple[int, str]]:
    """(code, value) of an enum, or of one field of a pattern."""
    if t.kind == "enum":
        return [(t.code(v), v) for v in t.values]
    assert group is not None
    vals = t.fields[group]
    codes = t.field_codes.get(group)
    return [(codes[i] if codes else i, v) for i, v in enumerate(vals)]


def code_width(pairs: list[tuple[int, str]]) -> int:
    return _clog2(max(c for c, _ in pairs) + 1)


def _lit(v: object) -> str:
    """AsciiDoc literal monospace that never interprets ~ ^ * etc."""
    return f"`+{v}+`"


# ---- diagrams ----------------------------------------------------------------------------


def _bits(value: int, width: int) -> str:
    return format(value, f"0{width}b")


def ascii_diagram(slots: list[Slot], values: dict[int, int] | None = None) -> str:
    """RISC-V style box diagram, MSB on the left.

    Row 1: bit range of each slot. Row 2: the box with slot labels. Row 3: the
    fixed value in binary (opcode, must-be-zero bits) or the slot width; with
    `values` (slot index -> value) the actual bits of an example word.
    """
    tops, labels, bottoms = [], [], []
    for i, s in enumerate(slots):
        tops.append(f"{s.hi}" if s.hi == s.lo else f"{s.hi}    {s.lo}")
        labels.append(s.label + ("!" if s.overflow else ""))
        if values is not None:
            bottoms.append(_bits(values[i], s.width))
        elif s.fixed is not None:
            bottoms.append(_bits(s.fixed, s.width))
        else:
            bottoms.append(str(s.width))
    widths = [max(len(t), len(lab), len(b)) + 2 for t, lab, b in zip(tops, labels, bottoms,
                                                                       strict=True)]
    border = "+" + "+".join("-" * w for w in widths) + "+"
    top = " " + " ".join(t.center(w) for t, w in zip(tops, widths, strict=True))
    mid = "|" + "|".join(lab.center(w) for lab, w in zip(labels, widths, strict=True)) + "|"
    bot = " " + " ".join(b.center(w) for b, w in zip(bottoms, widths, strict=True))
    return "\n".join([top.rstrip(), border, mid, border, bot.rstrip()])


field_index_of: dict[str, int] = {}  # set by render() for the ISA being documented


def wavedrom(slots: list[Slot], word_bits: int) -> str:
    lanes: list[dict[str, Any]] = []
    for s in reversed(slots):  # WaveDrom reg lists fields LSB first
        e: dict[str, Any] = {"bits": s.width}
        if s.kind == "reserved" or (s.fixed == 0 and s.kind != "opcode"):
            e["type"] = 1
            if s.label != "-":
                e["name"] = s.label
        else:
            e["name"] = s.label
            e["type"] = 2 + field_index_of.get(s.field, 0) % 6
        if s.kind == "opcode" and s.fixed is not None:
            e["attr"] = _bits(s.fixed, s.width)
        lanes.append(e)
    cfg = {"bits": word_bits, "lanes": 1, "hspace": 900}
    return "{reg: " + json.dumps(lanes) + ", config: " + json.dumps(cfg) + "}"


# ---- encoding rules ------------------------------------------------------------------------


def _type_anchor(t: OperandType) -> str:
    return f"<<type-{t.name.replace(':', '-')},{t.name}>>"


def _rule(s: Slot, isa: Isa, imem: int) -> str:
    if s.kind == "opcode":
        return f"opcode {s.fixed} (see <<opcode-map>>)"
    if s.kind == "reserved":
        return "reserved, must be 0"
    if s.kind == "fixed":
        return f"fixed: {_bits(s.fixed or 0, s.width)}"
    if s.kind == "delay_side":
        return ("side-set bits (MSB first, under an enable bit with `.side_set n opt`), then "
                "the `[n]` delay in the bits left; the split is set by the program's "
                "`.side_set`")
    if s.kind == "delay":
        return ("delay cycles, `[n]` modifier, 0.." + str((1 << s.width) - 1)
                if s.fixed is None else "must be 0 (no `[delay]` modifier)")
    if s.kind == "side":
        if s.fixed is not None:
            return "must be 0 (no side-set modifier)"
        return ("side-set value; width set by the program's `.side_set n [opt]` "
                "(`opt` adds a leading enable bit); unused bits 0")
    assert s.operand is not None
    t = s.operand.type
    if t.kind == "int":
        bias = f", stored as value minus {t.bias}" if t.bias else ""
        wrap = f", {t.max} written as 0" if t.max == 1 << s.width else ""
        return f"unsigned integer {t.min}..{t.max}{bias}{wrap}"
    if t.kind == "label":
        return f"instruction address 0..{imem - 1} (label)"
    if t.kind == "enum":
        return f"{'code' if t.codes else 'index'} of value, see {_type_anchor(t)}"
    if t.kind == "flag":
        return f"1 = `{t.word}` present"
    if t.kind == "pattern":
        what = "code" if s.group in t.field_codes else "index"
        return f"{what} of `{s.group}` value, see {_type_anchor(t)}"
    if t.kind == "cond":
        if s.group == "arg":
            return "condition argument (e.g. `evt n`), 0 if unused"
        return "condition code, see <<conditions>>"
    if t.kind == "ref":
        return f"0 = none, n = n-th `.{t.table}` config (definition order)"
    return t.kind


def _operand_table(slots: list[Slot], isa: Isa, imem: int) -> list[str]:
    out = ['[cols="2,1,1,6",options="header"]', "|===", "| Slot | Bits | Width | Encoding"]
    for s in slots:
        rng = f"{s.hi}" if s.hi == s.lo else f"{s.hi}:{s.lo}"
        out.append(f"| {_lit(s.label)} | {rng} | {s.width} | {_rule(s, isa, imem)}")
    out.append("|===")
    return out


# ---- examples ---------------------------------------------------------------------------------


def _examples() -> dict[str, tuple[ProgramImage, IrInstr, int]]:
    """First use of each instruction in the shipped programs (with its word)."""
    found: dict[str, tuple[ProgramImage, IrInstr, int]] = {}
    pack = active()
    for p in sorted(pack.programs.glob(f"*{pack.ext}")):
        image = assemble_file(p)
        for ins, word in zip(image.instructions, image.words, strict=True):
            if ins.name not in found and word is not None:
                found[ins.name] = (image, ins, word)
    return found


def _example(spec: InstrSpec, isa: Isa, ex: tuple[ProgramImage, IrInstr, int] | None
             ) -> list[str]:
    if ex is None:
        return ["_No example program uses this instruction yet._", ""]
    image, ins, word = ex
    enc = encoder_for(isa)
    slots = enc.instr_layout(spec, isa, image.imem_size, enc.side_layout(image.config))
    values = {i: (word >> s.lo) & ((1 << s.width) - 1) for i, s in enumerate(slots)}
    src = Path(ins.src).name
    return [f"Example, `{src}` (program `{image.name}`): `{ins.text}` -> "
            f"`0x{word:0{(isa.encoding.word_bits + 3) // 4}x}`", "",
            "....", ascii_diagram(slots, values), "....", ""]


# ---- document -----------------------------------------------------------------------------------


def render(isa: Isa) -> str:
    enc = encoder_for(isa)
    imem = int(isa.params["imem_size"])
    wb = isa.encoding.word_bits
    ops = by_opcode(isa)
    n_ops = 1 << isa.encoding.width("opcode")
    fixed_fields = structural(isa)
    field_index_of.clear()
    field_index_of.update({n: i for i, (n, _) in enumerate(isa.encoding.fields)})
    examples = _examples()
    draft = "" if isa.encoding.strict else " *DRAFT.*"
    out = [
        f"= {active().title}: instruction encoding",
        ":toc: left",
        ":toclevels: 2",
        ":sectnums:",
        "",
        f"{isa.name}, ISA version {isa.version}.{draft} Generated from "
        f"`{_spec_path()}` by `pioscope isa-docs`; do not edit.",
        "",
        "Diagrams show the most significant bit on the left. Under each box: the fixed "
        "value in binary, or the slot width in bits. `-` slots are reserved and must be 0; "
        "a `!` marks a field whose contents do not fit. The WaveDrom figures render with "
        "asciidoctor-diagram or a Kroki server.",
        "",
        "== Word format",
        "",
        f"Every instruction is one {wb}-bit word made of fixed fields. Each instruction "
        "packs its operands into these fields, MSB first.",
        "",
    ]
    common, top = [], wb
    for name, width in isa.encoding.fields:
        common.append(Slot(name, top - 1, top - width, "operand", name))
        top -= width
    out += ["....", ascii_diagram(common), "....", "",
            "[wavedrom, target=enc-word, format=svg]", "....", wavedrom(common, wb), "....", "",
            '[cols="1,1,1,5",options="header"]', "|===", "| Field | Bits | Width | Role"]
    for s in common:
        out.append(f"| `{s.label}` | {s.hi}:{s.lo} | {s.width} | "
                   f"{isa.encoding.roles.get(s.label, '')}")
    tier_col = bool(isa.tiers)
    out += ["|===", "", "[[opcode-map]]", "== Opcode map", "",
            f'[cols="1,1,2{",1" if tier_col else ""},1",options="header"]', "|===",
            "| Opcode | Binary | Mnemonic " + ("| Tier " if tier_col else "") + "| Status"]
    for op in range(n_ops):
        owners = ops.get(op, [])
        b = _bits(op, isa.encoding.width("opcode"))
        if not owners:
            out.append(f"| {op} | `{b}` | _free_ " + ("| " if tier_col else "") + "| ")
            continue
        names = ", ".join(f"<<instr-{s.name.replace('.', '-')},`{s.name}`>>" for s in owners)
        tier = f"| {owners[0].tier} " if tier_col else ""
        out.append(f"| {op} | `{b}` | {names} {tier}| {owners[0].status}")
    free = [op for op in range(n_ops) if op not in ops]
    out += ["|===", "", f"{len(free)} of {n_ops} opcodes are free.", ""]

    # pressure
    out += ["== Encoding pressure", "",
            "Bits each instruction uses in each field (structural fields at full width). "
            "*Bold* = field exactly full; `!` = overflow.", "",
            '[cols="2' + ",1" * len(isa.encoding.fields) + '",options="header"]', "|===",
            "| Instruction " + " ".join(f"| {n} ({w})" for n, w in isa.encoding.fields)]
    full: dict[str, list[str]] = {}
    overflow: list[str] = []
    for ins in isa.instructions.values():
        cells = []
        for f, (used, width) in enc.field_usage(ins, isa, imem).items():
            if used > width:
                cells.append(f"`!` {used}")
                overflow.append(f"`{ins.name}`.{f}")
            elif used == width and f not in fixed_fields:
                cells.append(f"*{used}*")
                full.setdefault(f, []).append(ins.name)
            else:
                cells.append(str(used))
        out.append(f"| `{ins.name}` " + " ".join(f"| {c}" for c in cells))
    out += ["|===", ""]

    # instructions
    for _tier, title, specs in tier_groups(isa):
        out += [f"== {title}", ""]
        for ins in specs:
            slots = enc.instr_layout(ins, isa, imem)
            mods = ", ".join(sorted(ins.modifiers)) or "none"
            out += [f"[[instr-{ins.name.replace('.', '-')}]]", f"=== `{ins.name}`", "",
                    f"Syntax: {_lit(syntax(ins.name, ins.operands))} +",
                    f"Opcode {ins.opcode} · status {ins.status} · modifiers: {mods}", "",
                    ins.semantics, "", "....", ascii_diagram(slots), "....", "",
                    f"[wavedrom, target=enc-{ins.name.replace('.', '-')}, format=svg]", "....",
                    wavedrom(slots, wb), "....", ""]
            out += [*_operand_table(slots, isa, imem), ""]
            out += _example(ins, isa, examples.get(ins.name))

    # code tables
    out += ["== Code tables", "", "[[conditions]]", "=== Conditions", "",
            f"The condition code is {_clog2(len(isa.conditions))} bits; conditions with an "
            "argument carry it in the following `cond.arg` bits.", "",
            '[cols="1,2,2,5",options="header"]', "|===", "| Code | Condition | Argument "
            "| Meaning"]
    for i, c in enumerate(isa.conditions.values()):
        arg = f"{c.arg.name} ({type_bits(c.arg, isa, imem)} bits)" if c.arg else ""
        out.append(f"| {i} | {_lit(c.name)} | {arg} | {c.semantics}")
    out += ["|===", ""]
    used_types = {o.type.name: o.type for s in isa.instructions.values() for o in s.operands
                  if o.type.kind in ("enum", "pattern")}
    for name in sorted(used_types):
        t = used_types[name]
        out += [f"[[type-{name.replace(':', '-')}]]", f"=== `{name}`", ""]
        groups = [None] if t.kind == "enum" else list(t.fields)
        for g in groups:
            pairs = enum_codes(t, g)
            w = code_width(pairs)
            head = f"`{g}` ({w} bits)" if g else f"{w} bits"
            out += [head, "", '[cols="1,1,3",options="header"]', "|===",
                    "| Code | Binary | Value"]
            for code, v in pairs:
                out.append(f"| {code} | `{_bits(code, w)}` | {_lit(v)}")
            out += ["|===", ""]

    # open points
    out += ["== Notes", ""]
    fits = ", ".join(overflow) if overflow else "none, every instruction fits"
    out.append(f"* *Overflow:* {fits} in {wb} bits.")
    for f, full_in in full.items():
        out.append(f"* *`{f}` field is full* for: {', '.join(f'`{n}`' for n in full_in)}.")
    content = [(f, w) for f, w in isa.encoding.fields if f not in fixed_fields]
    spare = {f: w - max(enc.field_usage(s, isa, imem)[f][0] for s in isa.instructions.values())
             for f, w in content}
    out.append("* *Headroom* (bits left in the most tightly packed instruction): "
               + ", ".join(f"`{f}` {n} of {w}" for (f, w), n in
                           zip(content, spare.values(), strict=True)) + ".")
    out.append(f"* *Opcodes:* {len(free)} of {n_ops} free.")
    out += [f"* {n}" for n in isa.encoding.notes]
    out.append("")
    return "\n".join(out)


def _spec_path() -> str:
    from sim.packs import ROOT

    return str(active().spec.relative_to(ROOT))


def main() -> None:
    out = out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(default_isa()))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

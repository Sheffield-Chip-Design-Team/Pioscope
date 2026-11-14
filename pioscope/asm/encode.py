"""Binary encoding: a separate, swappable pass over the symbolic IR (ADR-0005).

This module is the generic *field encoder*: each operand is packed MSB-first into the
encoding field named by its spec, in declaration order, with widths from the ISA yaml.
An ISA pack with a fixed binary format names its own encoder module under
`hooks: encoder:` (ADR-0012); `encoder_for(isa)` returns whichever applies. An encoder
module provides `encode_image`, `decode_word`, and optionally `delay_max(isa, config)`
and `instr_layout` (for the generated encoding documents).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pioscope.image import IrInstr, ProgramImage
from pioscope.isa import InstrSpec, Isa, OperandSpec, OperandType


class EncodeError(Exception):
    pass


@dataclass(frozen=True)
class Overflow(EncodeError):
    instr: str
    field: str
    needed: int
    width: int

    def __str__(self) -> str:
        return (
            f"'{self.instr}' needs {self.needed} bits in the '{self.field}' field, "
            f"which is {self.width} bits wide"
        )


def _clog2(n: int) -> int:
    return max(1, (n - 1).bit_length())


def type_bits(t: OperandType, isa: Isa, imem_size: int) -> int:
    k = t.kind
    if k == "int":
        if t.bits is None:
            raise EncodeError(f"type '{t.name}' has no encoding width (bits:)")
        return t.bits
    if k == "label":
        return _clog2(imem_size)
    if k == "enum":
        return _clog2(max(t.codes) + 1 if t.codes else len(t.values))
    if k == "flag":
        return 1
    if k == "cond":
        args = [c.arg for c in isa.conditions.values() if c.arg is not None]
        return _clog2(len(isa.conditions)) + max((type_bits(a, isa, imem_size) for a in args),
                                                 default=0)
    if k == "pattern":
        if not t.fields or any(not v for v in t.fields.values()):
            raise EncodeError(f"pattern type '{t.name}' is not encodable")
        return sum(_clog2(len(v)) for v in t.fields.values())
    if k == "ref":
        if t.bits is None:
            raise EncodeError(f"ref type '{t.name}' has no encoding width (bits:)")
        return t.bits
    raise EncodeError(f"type kind '{k}' is not encodable")


def _ref_names(config: dict[str, Any], table: str, isa: Isa) -> list[str]:
    builtin = list(isa.builtin_refs.get(table, ()))
    return builtin + [n for n in config.get(table, {}) if n not in builtin]


def _value_to_int(
    t: OperandType, v: Any, isa: Isa, imem_size: int, config: dict[str, Any]
) -> int:
    k = t.kind
    if k == "int":
        return int(v) - t.bias
    if k == "label":
        return int(v)
    if k == "enum":
        return t.code(str(v))
    if k == "flag":
        return 1 if v else 0
    if k == "cond":
        name, _, arg = str(v).partition(" ")
        conds = list(isa.conditions)
        argbits = type_bits(t, isa, imem_size) - _clog2(len(conds))
        return (conds.index(name) << argbits) | (int(arg) if arg else 0)
    if k == "pattern":
        out = 0
        for g, allowed in t.fields.items():
            out = (out << _clog2(len(allowed))) | allowed.index(str(v[g]))
        return out
    if k == "ref":
        assert t.table is not None
        return 0 if v is None else _ref_names(config, t.table, isa).index(v) + 1
    raise EncodeError(f"type kind '{k}' is not encodable")


def _int_to_value(
    t: OperandType, n: int, isa: Isa, imem_size: int, config: dict[str, Any]
) -> Any:
    k = t.kind
    if k == "int":
        return n + t.bias
    if k == "label":
        return n
    if k == "enum":
        return t.values[t.codes.index(n)] if t.codes else t.values[n]
    if k == "flag":
        return bool(n)
    if k == "cond":
        conds = list(isa.conditions)
        argbits = type_bits(t, isa, imem_size) - _clog2(len(conds))
        c = isa.conditions[conds[n >> argbits]]
        return c.name if c.arg is None else f"{c.name} {n & ((1 << argbits) - 1)}"
    if k == "pattern":
        out: dict[str, Any] = {}
        widths = [(g, allowed, _clog2(len(allowed))) for g, allowed in t.fields.items()]
        for g, allowed, w in reversed(widths):
            out[g] = allowed[n & ((1 << w) - 1)]
            n >>= w
        return dict(sorted(out.items(), key=lambda kv: list(t.fields).index(kv[0])))
    if k == "ref":
        assert t.table is not None
        return None if n == 0 else _ref_names(config, t.table, isa)[n - 1]
    raise EncodeError(f"type kind '{k}' is not decodable")


def side_layout(config: dict[str, Any]) -> tuple[int, bool]:
    """(count, opt) of a program's .side_set, for `instr_layout`."""
    ss = config.get("side_set") or {}
    return int(ss.get("count", 0)), bool(ss.get("opt", False))


_side_layout = side_layout


# ---- the single-source layout --------------------------------------------------------
# Every encoding field is packed MSB-first with a sequence of items, left-aligned;
# unused low bits are reserved (zero). The encoder, the decoder and the generated
# encoding document (docs/isa-encoding.adoc) all derive from _packing().


@dataclass(frozen=True)
class _Item:
    kind: str  # opcode | operand | delay | side_en | side
    bits: int
    operand: OperandSpec | None = None


def _packing(spec: InstrSpec, isa: Isa, imem_size: int,
             side_bits: tuple[int, bool] | None) -> dict[str, list[_Item]]:
    """Items per encoding field, in packing order. `side_bits` = (count, opt) from a
    program's .side_set; None means 'the whole side field' (for documentation)."""
    fields: dict[str, list[_Item]] = {n: [] for n, _ in isa.encoding.fields}
    fields["opcode"].append(_Item("opcode", isa.encoding.width("opcode")))
    for o in spec.operands:
        fields[o.field].append(_Item("operand", type_bits(o.type, isa, imem_size), o))
    if "delay" in fields:
        fields["delay"].append(_Item("delay", isa.encoding.width("delay")))
    if "side" in fields:
        if side_bits is None:
            fields["side"].append(_Item("side", isa.encoding.width("side")))
        else:
            count, opt = side_bits
            if count:
                if opt:
                    fields["side"].append(_Item("side_en", 1))
                fields["side"].append(_Item("side", count))
    return fields


@dataclass(frozen=True)
class Slot:
    """A contiguous bit range of one instruction's encoding."""

    label: str  # opcode, cond, cond.arg, route.src, count, delay, side, '-' (reserved)
    hi: int  # absolute bit positions in the word (hi >= lo)
    lo: int
    kind: str  # opcode | operand | delay | side | delay_side | fixed | reserved
    field: str  # the encoding field it lives in
    operand: OperandSpec | None = None
    group: str | None = None  # pattern group name, or "arg" for a condition argument
    fixed: int | None = None  # constant value (opcode; delay/side of instrs without them)
    overflow: bool = False  # the field's items do not fit its width

    @property
    def width(self) -> int:
        return self.hi - self.lo + 1


def _subslots(o: OperandSpec, isa: Isa, imem_size: int,
              bits: int) -> list[tuple[str, int, str | None]]:
    t = o.type
    if t.kind == "pattern":
        return [(f"{o.name}.{g}", _clog2(len(vals)), g) for g, vals in t.fields.items()]
    if t.kind == "cond":
        idx = _clog2(len(isa.conditions))
        return [(o.name, idx, None)] + ([(f"{o.name}.arg", bits - idx, "arg")]
                                        if bits > idx else [])
    return [(o.name, bits, None)]


def instr_layout(spec: InstrSpec, isa: Isa, imem_size: int,
                 side_bits: tuple[int, bool] | None = None) -> list[Slot]:
    """The bit layout of `spec`, MSB first. Overflowing fields are still laid out
    (their slots run past the field and are marked `overflow`)."""
    slots: list[Slot] = []
    top = isa.encoding.word_bits
    for fname, width in isa.encoding.fields:
        items = _packing(spec, isa, imem_size, side_bits)[fname]
        used = sum(i.bits for i in items)
        over = used > width
        pos = top
        for it in items:
            if it.kind == "operand":
                assert it.operand is not None
                for label, bits, group in _subslots(it.operand, isa, imem_size, it.bits):
                    slots.append(Slot(label, pos - 1, pos - bits, "operand", fname,
                                      it.operand, group, overflow=over))
                    pos -= bits
                continue
            if it.kind == "opcode":
                slot = Slot("opcode", pos - 1, pos - it.bits, "opcode", fname,
                            fixed=spec.opcode, overflow=over)
            elif it.kind == "delay":
                has = "delay" in spec.modifiers
                slot = Slot("delay" if has else "0", pos - 1, pos - it.bits, "delay", fname,
                            fixed=None if has else 0, overflow=over)
            else:  # side_en / side
                has = "side" in spec.modifiers
                label = ("side.en" if it.kind == "side_en" else "side") if has else "0"
                slot = Slot(label, pos - 1, pos - it.bits, "side", fname,
                            fixed=None if has else 0, overflow=over)
            slots.append(slot)
            pos -= it.bits
        if not over and pos > top - width:
            slots.append(Slot("-", pos - 1, top - width, "reserved", fname, fixed=0))
        top -= width
    return slots


def field_usage(spec: InstrSpec, isa: Isa, imem_size: int) -> dict[str, tuple[int, int]]:
    """field -> (bits used, width), with the side field counted at full width."""
    pk = _packing(spec, isa, imem_size, None)
    return {f: (sum(i.bits for i in pk[f]), w) for f, w in isa.encoding.fields}


def encode_instr(ins: IrInstr, isa: Isa, imem_size: int, config: dict[str, Any]) -> int:
    spec = isa.instructions[ins.name]
    packing = _packing(spec, isa, imem_size, _side_layout(config))
    word = 0
    for fname, width in isa.encoding.fields:
        value = used = 0
        for it in packing[fname]:
            if it.kind == "opcode":
                v = spec.opcode
            elif it.kind == "operand":
                assert it.operand is not None
                v = _value_to_int(it.operand.type, ins.operands[it.operand.name], isa,
                                  imem_size, config)
            elif it.kind == "delay":
                v = ins.delay
            elif it.kind == "side_en":
                v = 1 if ins.side is not None else 0
            else:
                v = ins.side or 0
            value = (value << it.bits) | v
            used += it.bits
        if used > width:
            raise Overflow(ins.name, fname, used, width)
        word = (word << width) | (value << (width - used))
    return word


def decode_word(
    word: int, isa: Isa, imem_size: int, config: dict[str, Any]
) -> tuple[str, dict[str, Any], int | None, int]:
    """Inverse of encode_instr: (name, operands, side, delay)."""
    shift = isa.encoding.word_bits - isa.encoding.width("opcode")
    opcode = (word >> shift) & ((1 << isa.encoding.width("opcode")) - 1)
    by_op = {s.opcode: s for s in isa.instructions.values()}
    if opcode not in by_op:
        raise EncodeError(f"unknown opcode {opcode}")
    spec = by_op[opcode]
    packing = _packing(spec, isa, imem_size, _side_layout(config))
    ops: dict[str, Any] = {}
    delay = 0
    side_en, side_v, has_side = 1, 0, False
    top = isa.encoding.word_bits
    for fname, width in isa.encoding.fields:
        pos = top
        for it in packing[fname]:
            pos -= it.bits
            v = (word >> pos) & ((1 << it.bits) - 1)
            if it.kind == "operand":
                assert it.operand is not None
                ops[it.operand.name] = _int_to_value(it.operand.type, v, isa, imem_size, config)
            elif it.kind == "delay":
                delay = v
            elif it.kind == "side_en":
                side_en = v
            elif it.kind == "side":
                side_v, has_side = v, True
        top -= width
    side = side_v if has_side and side_en and "side" in spec.modifiers else None
    return spec.name, ops, side, delay


def encode_image(image: ProgramImage, isa: Isa) -> tuple[list[int | None], list[str]]:
    words: list[int | None] = []
    problems: list[str] = []
    for ins in image.instructions:
        try:
            words.append(encode_instr(ins, isa, image.imem_size, image.config))
        except EncodeError as e:
            words.append(None)
            problems.append(f"{ins.src}: {e}")
    return words, problems


def delay_max(isa: Isa, config: dict[str, Any]) -> int:
    """Largest [delay] for a program with this config (the generic encoder: a constant)."""
    return int(isa.params["delay_max"])


def encoder_for(isa: Isa) -> Any:
    """The encoder module for `isa`: its `hooks: encoder:`, or this module."""
    import sys

    return isa.hook("encoder") or sys.modules[__name__]


def operand_bits(spec: OperandSpec, isa: Isa, imem_size: int) -> int:
    return type_bits(spec.type, isa, imem_size)

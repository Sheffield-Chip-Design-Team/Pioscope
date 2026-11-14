"""The RP2040 PIO instruction format (16 bits), as a pack encoder (ADR-0012).

    15..13  opcode (the instruction's `opcode:` in spec/isa/pio.yaml)
    12..8   delay / side-set: [enable if `.side_set .. opt`] [side-set value] [delay]
     7..0   arguments, per instruction (below)

Enum and pattern encodings come from the yaml's `codes:`, conditions from their order
under `conditions:`. A count of 32 is written as 0. `nop` is `mov y, y`, which is also
how 0xA042 disassembles.
"""

from __future__ import annotations

from typing import Any

from sim.asm.encode import EncodeError
from sim.image import IrInstr, ProgramImage
from sim.isa import Isa

FIELD_BITS = 5  # the delay/side-set field
NOP_ARG = 0x42  # mov y, y


def side_config(config: dict[str, Any]) -> tuple[int, bool, bool]:
    """(count, opt, pindirs) from the program's .side_set."""
    ss = config.get("side_set") or {}
    return int(ss.get("count", 0)), bool(ss.get("opt", False)), bool(ss.get("pindirs", False))


def delay_bits(config: dict[str, Any]) -> int:
    count, opt, _ = side_config(config)
    return FIELD_BITS - count - int(opt)


def delay_max(isa: Isa, config: dict[str, Any]) -> int:
    """The largest [delay] once side-set has taken its bits."""
    return (1 << delay_bits(config)) - 1


def _ds_field(ins: IrInstr, config: dict[str, Any]) -> int:
    count, opt, _ = side_config(config)
    dbits = delay_bits(config)
    if ins.delay >> dbits:
        raise EncodeError(f"delay {ins.delay} does not fit in {dbits} bits")
    v = ins.delay
    if count:
        side = ins.side if ins.side is not None else 0
        v |= side << dbits
        if opt and ins.side is not None:
            v |= 1 << (dbits + count)
    return v


def _count(n: int) -> int:
    return int(n) & 31


def _irq_index(n: int, rel: bool, what: str) -> int:
    if not 0 <= n <= 7:
        raise EncodeError(f"{what} index {n} is not 0..7")
    return n | (0x10 if rel else 0)


def encode_args(ins: IrInstr, isa: Isa) -> int:
    o = ins.operands
    t = {op.name: op.type for op in isa.instructions[ins.name].operands}
    name = ins.name
    if name == "jmp":
        return (list(isa.conditions).index(o["cond"]) << 5) | (int(o["target"]) & 31)
    if name == "wait":
        src = o["src"]
        idx = int(o["idx"])
        if src == "irq":
            idx = _irq_index(idx, bool(o["rel"]), "wait irq")
        elif o["rel"]:
            raise EncodeError(f"'rel' applies to wait irq only, not wait {src}")
        return (t["pol"].code(o["pol"]) << 7) | (t["src"].code(src) << 5) | idx
    if name in ("in", "out"):
        key = "src" if name == "in" else "dst"
        return (t[key].code(o[key]) << 5) | _count(o["count"])
    if name in ("push", "pull"):
        flag = "iffull" if name == "push" else "ifempty"
        return ((name == "pull") << 7) | (bool(o[flag]) << 6) | (t["mode"].code(o["mode"]) << 5)
    if name == "mov":
        st = t["src"]
        src = o["src"]
        op_code = st.field_codes["op"][st.fields["op"].index(src["op"])]
        src_code = st.field_codes["src"][st.fields["src"].index(src["src"])]
        return (t["dst"].code(o["dst"]) << 5) | (op_code << 3) | src_code
    if name == "nop":
        return NOP_ARG
    if name == "irq":  # op code: bit 6 = clear, bit 5 = wait
        return (t["op"].code(o["op"]) << 5) | _irq_index(int(o["n"]), bool(o["rel"]), "irq")
    if name == "set":
        return (t["dst"].code(o["dst"]) << 5) | int(o["value"])
    raise EncodeError(f"no RP2040 encoding for '{name}'")


def encode_instr(ins: IrInstr, isa: Isa, config: dict[str, Any]) -> int:
    spec = isa.instructions[ins.name]
    return (spec.opcode << 13) | (_ds_field(ins, config) << 8) | encode_args(ins, isa)


def encode_image(image: ProgramImage, isa: Isa) -> tuple[list[int | None], list[str]]:
    words: list[int | None] = []
    problems: list[str] = []
    for ins in image.instructions:
        try:
            words.append(encode_instr(ins, isa, image.config))
        except EncodeError as e:
            words.append(None)
            problems.append(f"{ins.src}: {e}")
    return words, problems


def _by_code(t: Any, code: int, what: str) -> str:
    if code not in t.codes:
        raise EncodeError(f"{what} code {code} is not an RP2040 encoding")
    return str(t.values[t.codes.index(code)])


def decode_word(word: int, isa: Isa, imem_size: int = 32,
                config: dict[str, Any] | None = None) -> tuple[str, dict[str, Any],
                                                             int | None, int]:
    """Inverse of encode_instr: (name, operands, side, delay)."""
    config = config or {}
    word &= 0xFFFF
    opcode, ds, arg = word >> 13, (word >> 8) & 31, word & 0xFF
    count, opt, _ = side_config(config)
    dbits = delay_bits(config)
    delay = ds & ((1 << dbits) - 1)
    side: int | None = None
    if count:
        side_v = (ds >> dbits) & ((1 << count) - 1)
        side = side_v if not opt or (ds >> (dbits + count)) & 1 else None
    by_op: dict[int, list[str]] = {}
    for s in isa.instructions.values():
        by_op.setdefault(s.opcode, []).append(s.name)
    names = by_op.get(opcode, [])
    if not names:
        raise EncodeError(f"opcode {opcode} is not an instruction")
    if opcode == isa.instructions["push"].opcode:
        name = "pull" if arg & 0x80 else "push"
        if arg & 0x1F:
            raise EncodeError(f"0x{word:04x}: reserved bits set in {name}")
    elif opcode == isa.instructions["mov"].opcode:
        name = "nop" if arg == NOP_ARG else "mov"
    else:
        name = names[0]
    t = {op.name: op.type for op in isa.instructions[name].operands}
    ops: dict[str, Any] = {}
    if name == "jmp":
        ops = {"cond": list(isa.conditions)[arg >> 5], "target": arg & 31}
    elif name == "wait":
        src = _by_code(t["src"], (arg >> 5) & 3, "wait source")
        idx = arg & 31
        rel = False
        if src == "irq":
            if idx & 0x08:
                raise EncodeError(f"0x{word:04x}: reserved bit 3 set in a wait irq index")
            rel, idx = bool(idx & 0x10), idx & 7
        ops = {"pol": str(arg >> 7), "src": src, "idx": idx, "rel": rel}
    elif name in ("in", "out"):
        key = "src" if name == "in" else "dst"
        ops = {key: _by_code(t[key], arg >> 5, key), "count": (arg & 31) or 32}
    elif name in ("push", "pull"):
        flag = "iffull" if name == "push" else "ifempty"
        ops = {flag: bool(arg & 0x40), "mode": _by_code(t["mode"], (arg >> 5) & 1, "mode")}
    elif name == "mov":
        st = t["src"]
        op_i = st.field_codes["op"].index((arg >> 3) & 3) if (arg >> 3) & 3 in st.field_codes[
            "op"] else None
        if op_i is None:
            raise EncodeError(f"0x{word:04x}: mov operation {(arg >> 3) & 3} is reserved")
        src_code = arg & 7
        if src_code not in st.field_codes["src"]:
            raise EncodeError(f"0x{word:04x}: mov source {src_code} is reserved")
        ops = {"dst": _by_code(t["dst"], arg >> 5, "mov destination"),
               "src": {"op": st.fields["op"][op_i],
                       "src": st.fields["src"][st.field_codes["src"].index(src_code)]}}
    elif name == "irq":
        if arg & 0x88:
            raise EncodeError(f"0x{word:04x}: reserved bits set in irq")
        op = "clear" if arg & 0x40 else ("wait" if arg & 0x20 else "set")
        ops = {"op": op, "n": arg & 7, "rel": bool(arg & 0x10)}
    elif name == "set":
        ops = {"dst": _by_code(t["dst"], arg >> 5, "set destination"), "value": arg & 31}
    return name, ops, side, delay


def disassemble(word: int, isa: Isa, config: dict[str, Any] | None = None) -> str:
    """pioasm-style text for a word (for `exec` and the debugger)."""
    name, o, side, delay = decode_word(word, isa, 32, config)
    if name == "jmp":
        args = ([] if o["cond"] == "always" else [o["cond"]]) + [str(o["target"])]
    elif name == "wait":
        args = [o["pol"], o["src"], str(o["idx"])] + (["rel"] if o["rel"] else [])
    elif name in ("in", "out"):
        key = "src" if name == "in" else "dst"
        args = [f"{o[key]},", str(o["count"])]
    elif name in ("push", "pull"):
        flag = "iffull" if name == "push" else "ifempty"
        args = ([flag] if o[flag] else []) + [o["mode"]]
    elif name == "mov":
        op = "" if o["src"]["op"] == "none" else o["src"]["op"]
        args = [f"{o['dst']},", f"{op}{o['src']['src']}"]
    elif name == "irq":
        args = [o["op"], str(o["n"])] + (["rel"] if o["rel"] else [])
    elif name == "set":
        args = [f"{o['dst']},", str(o["value"])]
    else:
        args = []
    text = " ".join([name, *args])
    if side is not None:
        text += f" side {side}"
    if delay:
        text += f" [{delay}]"
    return text


# ---- layout, for the generated encoding documents (sim/scripts/gen_isa_*) -------------

_ARG_LAYOUT: dict[str, list[tuple[str, int, int | None]]] = {
    # (operand or label, width, fixed value) for bits 7..0, MSB first
    "jmp": [("cond", 3, None), ("target", 5, None)],
    "wait": [("pol", 1, None), ("src", 2, None), ("idx", 5, None)],
    "in": [("src", 3, None), ("count", 5, None)],
    "out": [("dst", 3, None), ("count", 5, None)],
    "push": [("0", 1, 0), ("iffull", 1, None), ("mode", 1, None), ("-", 5, 0)],
    "pull": [("1", 1, 1), ("ifempty", 1, None), ("mode", 1, None), ("-", 5, 0)],
    "mov": [("dst", 3, None), ("src.op", 2, None), ("src.src", 3, None)],
    "irq": [("0", 1, 0), ("op", 2, None), ("idx", 5, None)],
    "set": [("dst", 3, None), ("value", 5, None)],
    "nop": [("mov y, y", 8, NOP_ARG)],
}


def instr_layout(spec: Any, isa: Isa, imem_size: int,
                 side_bits: tuple[int, bool] | None = None) -> list[Any]:
    """The bit layout of `spec`, MSB first, as sim.asm.encode.Slot. With `side_bits`
    (a program's (count, opt)), the delay/side-set field is split accordingly."""
    from sim.asm.encode import Slot

    ops = {o.name: o for o in spec.operands}
    slots = [Slot("opcode", 15, 13, "opcode", "opcode", fixed=spec.opcode)]
    if side_bits is None or not side_bits[0]:
        label = "delay/side" if side_bits is None else "delay"
        slots.append(Slot(label, 12, 8, "delay_side", "delay_side"))
    else:
        count, opt = side_bits
        pos = 12
        if opt:
            slots.append(Slot("side.en", pos, pos, "side", "delay_side"))
            pos -= 1
        slots.append(Slot("side", pos, pos - count + 1, "side", "delay_side"))
        pos -= count
        if pos >= 8:
            slots.append(Slot("delay", pos, 8, "delay", "delay_side"))
    pos = 7
    for label, width, fixed in _ARG_LAYOUT[spec.name]:
        name, _, group = label.partition(".")
        op = ops.get(name)
        if label == "idx" and spec.name == "irq":
            op = ops.get("n")
        if fixed is not None or op is None:
            slots.append(Slot(label, pos, pos - width + 1, "reserved" if label == "-" else
                              "fixed", "operand", fixed=fixed if fixed is not None else 0))
        else:
            slots.append(Slot(label, pos, pos - width + 1, "operand", "operand", op,
                              group=group or None))
        pos -= width
    return slots


def field_usage(spec: Any, isa: Isa, imem_size: int) -> dict[str, tuple[int, int]]:
    """field -> (bits used, width). Every RP2040 instruction fills its word."""
    used = sum(s.width for s in instr_layout(spec, isa, imem_size)
               if s.field == "operand" and s.kind != "reserved")
    return {"opcode": (3, 3), "delay_side": (5, 5), "operand": (used, 8)}


def side_layout(config: dict[str, Any]) -> tuple[int, bool]:
    """(count, opt) of a program's .side_set, for `instr_layout`."""
    count, opt, _ = side_config(config)
    return count, opt

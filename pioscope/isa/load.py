"""Load and validate an ISA spec yaml (<pack>/isa.yaml)."""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from .spec import (
    TYPE_KINDS,
    CondSpec,
    DirectiveSpec,
    EncodingSpec,
    InstrSpec,
    Isa,
    OperandSpec,
    OperandType,
    PadGroup,
)


class IsaSpecError(Exception):
    """The ISA YAML is malformed or inconsistent."""


def _req(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d:
        raise IsaSpecError(f"{where}: missing required key '{key}'")
    return d[key]


def _type(name: str, raw: dict[str, Any]) -> OperandType:
    kind = _req(raw, "kind", f"operand_types.{name}")
    if kind not in TYPE_KINDS:
        raise IsaSpecError(f"operand_types.{name}: unknown kind '{kind}'")
    regex = None
    if kind == "pattern":
        try:
            regex = re.compile(_req(raw, "regex", f"operand_types.{name}"))
        except re.error as e:
            raise IsaSpecError(f"operand_types.{name}: bad regex: {e}") from e
    fields = {k: tuple(str(v) for v in vs) for k, vs in (raw.get("fields") or {}).items()}
    if regex is not None:
        for g in fields:
            if g not in regex.groupindex:
                raise IsaSpecError(f"operand_types.{name}: field '{g}' is not a regex group")
    values = tuple(str(v) for v in raw.get("values", ()))
    if kind == "enum" and not values:
        raise IsaSpecError(f"operand_types.{name}: enum needs values")
    codes: tuple[int, ...] = ()
    field_codes: dict[str, tuple[int, ...]] = {}
    raw_codes = raw.get("codes")
    if raw_codes is not None:
        if kind == "enum":
            codes = tuple(int(c) for c in raw_codes)
            if len(codes) != len(values):
                raise IsaSpecError(f"operand_types.{name}: codes must match values")
        elif kind == "pattern":
            field_codes = {str(g): tuple(int(c) for c in cs) for g, cs in raw_codes.items()}
            for g, cs in field_codes.items():
                if g not in fields or len(cs) != len(fields[g]):
                    raise IsaSpecError(f"operand_types.{name}: codes.{g} must match fields.{g}")
        else:
            raise IsaSpecError(f"operand_types.{name}: codes are for enum or pattern types")
    if kind == "int":
        lo, hi = raw.get("min"), raw.get("max")
        if lo is None or hi is None or lo > hi:
            raise IsaSpecError(f"operand_types.{name}: int needs min <= max")
        bits = raw.get("bits")
        if bits is not None and (hi - raw.get("bias", 0)) >= (1 << bits):
            raise IsaSpecError(f"operand_types.{name}: max {hi} does not fit in {bits} bits")
    return OperandType(
        name=name,
        kind=kind,
        min=raw.get("min"),
        max=raw.get("max"),
        bits=raw.get("bits"),
        bias=raw.get("bias", 0),
        values=values,
        word=raw.get("word"),
        regex=regex,
        fields=fields,
        ints=tuple(raw.get("ints", ())),
        table=raw.get("table"),
        codes=codes,
        field_codes=field_codes,
    )


def _operands(
    raw: list[dict[str, Any]], types: dict[str, OperandType], where: str
) -> tuple[OperandSpec, ...]:
    out: list[OperandSpec] = []
    seen: set[str] = set()
    for i, o in enumerate(raw or []):
        name = _req(o, "name", f"{where}.operands[{i}]")
        if name in seen:
            raise IsaSpecError(f"{where}: duplicate operand '{name}'")
        seen.add(name)
        tname = _req(o, "type", f"{where}.{name}")
        if tname == "flag" and tname not in types:
            t = OperandType(name=f"flag:{name}", kind="flag", word=name)
        elif tname not in types:
            raise IsaSpecError(f"{where}.{name}: unknown type '{tname}'")
        else:
            t = types[tname]
            if t.kind == "flag" and t.word is None:
                t = OperandType(name=t.name, kind="flag", word=name)
        out.append(
            OperandSpec(
                name=name,
                type=t,
                optional=bool(o.get("optional", False)),
                default=o.get("default"),
                key=o.get("key"),
                field=o.get("field", "operand"),
            )
        )
    # Positional optional operands must come before a required one only if the
    # binder can tell them apart; that is checked by tests, not here.
    return tuple(out)


def load_isa_dict(doc: dict[str, Any]) -> Isa:
    types = {n: _type(n, r) for n, r in (doc.get("operand_types") or {}).items()}
    if "flag" not in types:
        types["flag"] = OperandType(name="flag", kind="flag")

    conditions: dict[str, CondSpec] = {}
    for c in doc.get("conditions", []):
        name = str(_req(c, "name", "conditions"))
        if name in conditions:
            raise IsaSpecError(f"conditions: duplicate '{name}'")
        arg = c.get("arg")
        if arg is not None and arg not in types:
            raise IsaSpecError(f"conditions.{name}: unknown arg type '{arg}'")
        conditions[name] = CondSpec(name, c.get("semantics", ""), types[arg] if arg else None)

    enc_raw = _req(doc, "encoding", "root")
    encoding = EncodingSpec(
        fields=tuple((str(k), int(v)) for k, v in _req(enc_raw, "fields", "encoding").items()),
        strict=bool(enc_raw.get("strict", True)),
        roles={str(k): str(v) for k, v in (enc_raw.get("roles") or {}).items()},
        notes=tuple(str(n) for n in (enc_raw.get("notes") or [])),
    )
    params = dict(_req(doc, "params", "root"))
    if encoding.word_bits != params.get("word_bits", encoding.word_bits):
        raise IsaSpecError(
            f"encoding fields sum to {encoding.word_bits} bits, params.word_bits is "
            f"{params['word_bits']}"
        )
    field_names = {n for n, _ in encoding.fields}
    default_mods = frozenset(doc.get("default_modifiers", ["side", "delay"]))

    hooks = {str(k): str(v) for k, v in (doc.get("hooks") or {}).items()}
    unknown = set(hooks) - {"directives", "encoder"}
    if unknown:
        raise IsaSpecError(f"hooks: unknown key(s) {sorted(unknown)} (directives, encoder)")
    # a custom encoder may share major opcodes between instructions (PIO push/pull)
    unique_opcodes = "encoder" not in hooks

    instructions: dict[str, InstrSpec] = {}
    opcodes: dict[int, str] = {}
    for ins in _req(doc, "instructions", "root"):
        name = str(_req(ins, "name", "instructions"))
        where = f"instructions.{name}"
        if name in instructions:
            raise IsaSpecError(f"{where}: duplicate instruction")
        opcode = int(_req(ins, "opcode", where))
        if unique_opcodes and opcode in opcodes:
            raise IsaSpecError(f"{where}: opcode {opcode} already used by {opcodes[opcode]}")
        if opcode >= (1 << encoding.width("opcode")):
            raise IsaSpecError(f"{where}: opcode {opcode} does not fit the opcode field")
        opcodes[opcode] = name
        ops = _operands(ins.get("operands", []), types, where)
        for o in ops:
            if o.field not in field_names:
                raise IsaSpecError(f"{where}.{o.name}: unknown encoding field '{o.field}'")
        mods = frozenset(ins["modifiers"]) if "modifiers" in ins else default_mods
        instructions[name] = InstrSpec(
            name=name,
            tier=int(_req(ins, "tier", where)),
            opcode=opcode,
            operands=ops,
            modifiers=mods,
            semantics=str(_req(ins, "semantics", where)),
            status=str(ins.get("status", "stable")),
        )

    directives: dict[str, DirectiveSpec] = {}
    for d in _req(doc, "directives", "root"):
        name = str(_req(d, "name", "directives"))
        if not name.startswith("."):
            raise IsaSpecError(f"directives.{name}: directive names start with '.'")
        if name in directives:
            raise IsaSpecError(f"directives.{name}: duplicate")
        pat = d.get("pattern")
        directives[name] = DirectiveSpec(
            name=name,
            operands=_operands(d.get("operands", []), types, f"directives.{name}"),
            semantics=str(_req(d, "semantics", f"directives.{name}")),
            pattern=re.compile(pat) if pat else None,
            harness=bool(d.get("harness", False)),
        )

    target = _req(doc, "target", "root")
    pads = tuple(
        PadGroup(prefix=k, count=int(v["count"]), dir=str(v["dir"]))
        for k, v in _req(target, "pads", "target").items()
    )
    return Isa(
        version=str(_req(doc, "version", "root")),
        params=params,
        pads=pads,
        pin_modes=tuple(target.get("pin_modes", ["push_pull"])),
        flags=tuple(doc.get("flags", [])),
        builtin_refs={str(k): tuple(str(x) for x in v)
                      for k, v in (doc.get("builtin_refs") or {}).items()},
        types=types,
        conditions=conditions,
        instructions=instructions,
        directives=directives,
        encoding=encoding,
        hooks=hooks,
        name=str(doc.get("name", "ISA")),
        pack=str(doc.get("pack", "")),
        tiers={int(k): str(v) for k, v in (doc.get("tiers") or {}).items()},
        status=str(doc.get("status", "")),
    )


def default_spec() -> Path:
    """The active ISA pack's spec (pioscope.packs.active())."""
    from pioscope.packs import active

    return active().spec


def load_isa(path: Path | str | None = None) -> Isa:
    return _load(Path(path).resolve() if path is not None else default_spec())


@cache
def _load(p: Path) -> Isa:
    with p.open() as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict):
        raise IsaSpecError(f"{p}: top level must be a mapping")
    return load_isa_dict(doc)


def default_isa() -> Isa:
    """The active pack's ISA (cached per spec file)."""
    return _load(default_spec())

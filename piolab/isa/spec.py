"""Frozen data model of an ISA, built from its spec yaml (<pack>/isa.yaml) by
:mod:`piolab.isa.load`."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# A JSON-able operand value: int, str, bool, None, or a dict of those (pattern types).
Value = Any

TYPE_KINDS = frozenset({"int", "label", "cond", "enum", "flag", "pattern", "ref", "word", "rest"})


@dataclass(frozen=True)
class OperandType:
    name: str
    kind: str
    min: int | None = None
    max: int | None = None
    bits: int | None = None
    bias: int = 0
    values: tuple[str, ...] = ()
    word: str | None = None
    regex: re.Pattern[str] | None = None
    fields: dict[str, tuple[str, ...]] = field(default_factory=dict)
    ints: tuple[str, ...] = ()
    table: str | None = None
    #: Encoding of each enum value (same order as `values`); empty = the value's index.
    codes: tuple[int, ...] = ()
    #: Encoding of each pattern field's values (same order as `fields[g]`).
    field_codes: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def code(self, value: str) -> int:
        """The encoding of an enum value."""
        i = self.values.index(value)
        return self.codes[i] if self.codes else i

    def describe(self) -> str:
        """Human text for 'expected ...' in error messages."""
        if self.kind == "int":
            return f"an integer {self.min}..{self.max}"
        if self.kind == "enum":
            return "one of " + ", ".join(repr(v) for v in self.values)
        if self.kind == "flag":
            return f"'{self.word}'"
        if self.kind == "pattern":
            return f"{self.name} (matching {self.regex.pattern if self.regex else '?'})"
        if self.kind == "ref":
            return f"the name of a {self.table} config"
        if self.kind == "cond":
            return "a condition"
        return f"a {self.kind}"


@dataclass(frozen=True)
class OperandSpec:
    name: str
    type: OperandType
    optional: bool = False
    default: Value = None
    key: str | None = None
    field: str = "operand"


@dataclass(frozen=True)
class CondSpec:
    name: str
    semantics: str
    arg: OperandType | None = None


@dataclass(frozen=True)
class InstrSpec:
    name: str
    tier: int
    opcode: int
    operands: tuple[OperandSpec, ...]
    modifiers: frozenset[str]
    semantics: str
    status: str = "stable"


@dataclass(frozen=True)
class DirectiveSpec:
    name: str
    operands: tuple[OperandSpec, ...]
    semantics: str
    pattern: re.Pattern[str] | None = None
    #: A directive of the bench/harness, not of the ISA's own assembler (e.g. pin maps
    #: a C SDK would configure); documented separately.
    harness: bool = False


@dataclass(frozen=True)
class EncodingSpec:
    fields: tuple[tuple[str, int], ...]  # MSB -> LSB
    strict: bool
    #: What each field holds (for the generated encoding documents).
    roles: dict[str, str] = field(default_factory=dict)
    #: Points about the encoding listed in the generated documents.
    notes: tuple[str, ...] = ()

    @property
    def word_bits(self) -> int:
        return sum(w for _, w in self.fields)

    def width(self, name: str) -> int:
        for n, w in self.fields:
            if n == name:
                return w
        raise KeyError(name)


@dataclass(frozen=True)
class PadGroup:
    prefix: str
    count: int
    dir: str  # in | out | bidir


@dataclass(frozen=True)
class Isa:
    version: str
    params: dict[str, int]
    pads: tuple[PadGroup, ...]
    pin_modes: tuple[str, ...]
    flags: tuple[str, ...]
    #: Names a `ref` operand may use without a defining directive, per ref table.
    builtin_refs: dict[str, tuple[str, ...]]
    types: dict[str, OperandType]
    conditions: dict[str, CondSpec]
    instructions: dict[str, InstrSpec]
    directives: dict[str, DirectiveSpec]
    encoding: EncodingSpec
    #: Pack modules named by the yaml's `hooks:` (directives, encoder); see piolab.packs.
    hooks: dict[str, str] = field(default_factory=dict)
    #: Human name of the ISA (`name:` in the yaml), for generated documents.
    name: str = "ISA"
    #: The ISA pack this spec belongs to (`pack:` in the yaml; piolab.packs).
    pack: str = ""
    #: A status line for the generated reference (`status:` in the yaml).
    status: str = ""
    #: Tier number -> heading, for documents that group instructions by tier.
    tiers: dict[int, str] = field(default_factory=dict)

    def hook(self, key: str) -> Any:
        """The module named by hooks[key], imported, or None."""
        path = self.hooks.get(key)
        if not path:
            return None
        import importlib

        return importlib.import_module(path)

    def pad_names(self) -> list[str]:
        return [f"{g.prefix}{i}" for g in self.pads for i in range(g.count)]

    def pad_dir(self, pad: str) -> str:
        for g in self.pads:
            if pad.startswith(g.prefix) and pad[len(g.prefix) :].isdigit():
                return g.dir
        raise KeyError(pad)

    def find_directive(self, name: str) -> tuple[DirectiveSpec, dict[str, str]] | None:
        """Look up a directive, including pattern directives (`pattern:`, e.g. `.name0`)."""
        d = self.directives.get(name)
        if d is not None and d.pattern is None:
            return d, {}
        for d in self.directives.values():
            if d.pattern is not None:
                m = d.pattern.match(name)
                if m:
                    return d, m.groupdict()
        return None

"""ISA packs (ADR-0012): everything that belongs to one instruction set, in one place.

The framework (assembler, bench, devices, decoders, debugger, trace, VCD, arch study)
knows nothing about a particular ISA. A pack supplies:

- `spec`: the ISA yaml, the single source of truth for that ISA (agreement #1). Its
  `hooks:` section names the modules for pack-specific directive handlers
  and a custom binary encoder, so a loaded `Isa` is self-describing;
- `model`: the engine model, as `module:callable` (the DUT for `--dut engine`);
- `demand`: a module with the arch-study hooks `units_used`, `bit_shares`, `fifos`;
- `programs`, `arch`: its protocol programs and arch-study data;
- `docs`: where its generated documents go (isa.md, isa-encoding.*, arch-study.*).

Packs are discovered from the sub-packages of `pioscope.packs`; each defines `PACK`. The
active pack is `$PIOSCOPE_ISA` if set, else the discovered pack with the lowest `rank`.
Tests and tools that need a specific pack use `use(name)`.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENV = "PIOSCOPE_ISA"


@dataclass(frozen=True)
class IsaPack:
    name: str
    title: str  # e.g. "RP2040 PIO": used in generated documents
    spec: Path
    programs: Path
    arch: Path
    docs: Path
    model: str  # "module:callable" returning a Dut
    demand: str  # module with units_used / bit_shares / fifos
    ext: str = ".pio"  # source file suffix
    rank: int = 100  # lowest discovered rank is the default pack

    def demand_module(self) -> ModuleType:
        return importlib.import_module(self.demand)

    def model_factory(self) -> Any:
        mod, _, fn = self.model.partition(":")
        return getattr(importlib.import_module(mod), fn)

    def program(self, name: str) -> Path:
        """A program of this pack by stem (`uart_tx`) or file name."""
        p = self.programs / name
        return p if p.suffix else p.with_suffix(self.ext)


@cache
def packs() -> dict[str, IsaPack]:
    found: dict[str, IsaPack] = {}
    for m in pkgutil.iter_modules(__path__):
        if not m.ispkg:
            continue
        pack = getattr(importlib.import_module(f"{__name__}.{m.name}"), "PACK", None)
        if isinstance(pack, IsaPack):
            found[pack.name] = pack
    return dict(sorted(found.items(), key=lambda kv: (kv[1].rank, kv[0])))


_override: list[str] = []


def get(name: str) -> IsaPack:
    all_ = packs()
    if name not in all_:
        raise KeyError(f"unknown ISA pack '{name}' (available: {', '.join(all_)})")
    return all_[name]


def active() -> IsaPack:
    if _override:
        return get(_override[-1])
    env = os.environ.get(ENV)
    if env:
        return get(env)
    return next(iter(packs().values()))


def for_path(path: Path | str) -> IsaPack | None:
    """The pack whose source suffix `path` has, or None."""
    suffix = Path(path).suffix
    return next((p for p in packs().values() if p.ext == suffix), None)


def for_image(image: Any) -> IsaPack:
    """The pack a ProgramImage was assembled for, else the active pack."""
    name = getattr(image, "isa_pack", "") if image is not None else ""
    return get(name) if name and name in packs() else active()


@contextmanager
def use(name: str) -> Iterator[IsaPack]:
    """Make `name` the active pack inside the block (tests, tools)."""
    pack = get(name)
    _override.append(name)
    try:
        yield pack
    finally:
        _override.pop()


def select(name: str | None) -> None:
    """Make `name` the active pack for the rest of the process (`--isa` on the CLI)."""
    if name:
        get(name)
        _override[:] = [name]


__all__ = ["ENV", "ROOT", "IsaPack", "active", "for_image", "for_path", "get", "packs", "select",
           "use"]

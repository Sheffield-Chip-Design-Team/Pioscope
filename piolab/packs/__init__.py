"""ISA packs (ADR-0012): everything that belongs to one instruction set, in one place.

The framework (assembler, bench, devices, decoders, debugger, trace, VCD, arch study)
knows nothing about a particular ISA. A pack supplies:

- `spec`: the ISA yaml, the single source of truth for that ISA (agreement #1). Its
  `hooks:` section names the modules for pack-specific directive handlers
  and a custom binary encoder, so a loaded `Isa` is self-describing;
- `models`: its engine models by name, each `module:callable` (the DUT for `--dut engine`,
  or `--model NAME`); `default_model` is the one used when none is named. Packs that keep
  one sub-package per model (`<pkg>/models/<name>/`, each exporting `Engine`) use
  `models=discover_models("<pkg>.models")`, so a new model is a new folder;
- `scenarios`: optionally, a module of scenario factories, so `--scenario NAME` works;
- `demand`: a module with the arch-study hooks `units_used`, `bit_shares`, `fifos`;
- `programs`, `arch`: its protocol programs and arch-study data;
- `docs`: where its generated documents go (isa.md, isa-encoding.*, arch-study.*); by
  default `docs/<name>` under the working directory, i.e. the project being worked on.

Keep a pack's spec, programs and arch data inside its Python package (`pack_dir()`), so
that it works from an installed wheel as well as from a checkout.

Packs are discovered from the sub-packages of `piolab.packs` (the built-in ones) and
from the `piolab.packs` entry-point group, so another project's pack is found once it is
installed:

    [project.entry-points."piolab.packs"]
    myisa = "myisa.pack:PACK"

The active pack is `--isa`, else `$PIOLAB_ISA`, else `[tool.piolab] pack` in the project's
pyproject.toml (`piolab.config`), else the discovered pack with the lowest `rank`.
Tests and tools that need a specific pack use `use(name)`.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import pkgutil
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

ENV = "PIOLAB_ISA"
ENTRY_POINTS = "piolab.packs"
#: The attribute a model package exports for `discover_models`.
MODEL_ATTR = "Engine"


class UnknownModel(KeyError):
    def __str__(self) -> str:
        return str(self.args[0])


def discover_models(package: str, attr: str = MODEL_ATTR) -> dict[str, str]:
    """`{name: "package.name:attr"}` for each sub-package of `package` (e.g.
    `myisa_isa.models` → `{"base": "myisa_isa.models.base:Engine"}`). Only `package`
    itself is imported; each model is imported when it is first built."""
    spec = importlib.util.find_spec(package)
    if spec is None or not spec.submodule_search_locations:
        raise ModuleNotFoundError(f"model package '{package}' not found (or not a package)")
    return {m.name: f"{package}.{m.name}:{attr}"
            for m in sorted(pkgutil.iter_modules(spec.submodule_search_locations),
                            key=lambda m: m.name)
            if m.ispkg and not m.name.startswith("_")}


def pack_dir(module_file: str) -> Path:
    """The directory of a pack's module: `HERE = pack_dir(__file__)`."""
    return Path(module_file).resolve().parent


def display_path(path: Path) -> str:
    """`path` for documents and messages: relative to the working directory when it is
    under it, else from its top-level package down (stable across installs)."""
    path = Path(path).resolve()
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        pass
    for i, part in enumerate(path.parts):
        if part in ("site-packages", "dist-packages"):
            return Path(*path.parts[i + 1:]).as_posix()
    return path.as_posix()


@dataclass(frozen=True)
class IsaPack:
    name: str
    title: str  # e.g. "RP2040 PIO": used in generated documents
    spec: Path
    programs: Path
    arch: Path
    #: Engine models by name, each "module:callable" returning a Dut.
    models: Mapping[str, str]
    demand: str  # module with units_used / bit_shares / fifos
    ext: str = ".pio"  # source file suffix
    rank: int = 100  # lowest discovered rank is the default pack
    #: The model used when none is named (else the first one).
    default_model: str = "base"
    #: A module (or package of modules) of scenario factories, for `--scenario NAME`.
    scenarios: str | None = None
    #: Where generated documents go; relative paths are against the project directory.
    #: Default: <[tool.piolab] docs, else docs>/<name>.
    docs: Path | None = None

    def __post_init__(self) -> None:
        if not self.models:
            raise ValueError(f"ISA pack '{self.name}' has no models")
        object.__setattr__(self, "models", dict(self.models))
        if self.default_model not in self.models:
            object.__setattr__(self, "default_model", next(iter(self.models)))

    @property
    def docs_dir(self) -> Path:
        from piolab.config import load

        cfg = load()
        d = self.docs if self.docs is not None else (cfg.docs or Path("docs")) / self.name
        return d if d.is_absolute() else cfg.root / d

    def demand_module(self) -> ModuleType:
        return importlib.import_module(self.demand)

    def model_name(self, name: str | None = None) -> str:
        """`name` checked against this pack's models; None: the project's
        `[tool.piolab] model` if this pack has it, else `default_model`."""
        if name is None:
            from piolab.config import load

            cfg = load().model
            name = cfg if cfg is not None and cfg in self.models else self.default_model
        if name not in self.models:
            raise UnknownModel(f"ISA pack '{self.name}' has no model '{name}' "
                               f"(models: {', '.join(self.models)})")
        return name

    def model_spec(self, name: str | None = None) -> str:
        return self.models[self.model_name(name)]

    def model_factory(self, name: str | None = None) -> Any:
        mod, _, fn = self.model_spec(name).partition(":")
        return getattr(importlib.import_module(mod), fn)

    def model_module(self, name: str | None = None) -> ModuleType:
        """The module a model is defined in (holds its `HANDLERS`)."""
        return importlib.import_module(self.model_spec(name).partition(":")[0])

    def program(self, name: str) -> Path:
        """A program of this pack by stem (`uart_tx`) or file name."""
        p = self.programs / name
        return p if p.suffix else p.with_suffix(self.ext)

    def program_names(self) -> list[str]:
        return sorted(p.stem for p in self.programs.glob(f"*{self.ext}"))


@cache
def packs() -> dict[str, IsaPack]:
    """Every pack: the built-in sub-packages, then the installed entry points."""
    from importlib.metadata import entry_points

    found: dict[str, IsaPack] = {}
    for m in pkgutil.iter_modules(__path__):
        if not m.ispkg:
            continue
        pack = getattr(importlib.import_module(f"{__name__}.{m.name}"), "PACK", None)
        if isinstance(pack, IsaPack):
            found[pack.name] = pack
    for ep in entry_points(group=ENTRY_POINTS):
        pack = ep.load()
        if not isinstance(pack, IsaPack):
            raise TypeError(f"entry point {ep.name} = {ep.value} is not an IsaPack")
        if pack.name in found and found[pack.name] is not pack:
            raise ValueError(f"two ISA packs are called '{pack.name}'")
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
    from piolab.config import load

    cfg = load().pack
    if cfg:
        return get(cfg)
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


__all__ = ["ENTRY_POINTS", "ENV", "MODEL_ATTR", "IsaPack", "UnknownModel", "active",
           "discover_models", "display_path", "for_image", "for_path", "get", "pack_dir", "packs",
           "select", "use"]

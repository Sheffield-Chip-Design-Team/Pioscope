"""Project configuration: the `[tool.piolab]` table of the nearest `pyproject.toml`.

A downstream project sets its defaults once, so day-to-day commands need no flags:

    [tool.piolab]
    pack = "myisa"         # default ISA pack (--isa > $PIOLAB_ISA > this > lowest rank)
    model = "base"         # default engine model for sim / debug / dap / pytest
    cycles = 100000        # default --cycles
    out = "build"          # where a bare --vcd / --listing, the vsix and arch html go
    docs = "docs"          # generated documents: <docs>/<pack> (else the pack's own)

    [tool.piolab.devices]  # default options per device, under the CLI's
    uart = { baud = 115200 }

The file is found by walking up from the working directory; with none (or no table),
every field has its default. Relative paths are against the pyproject's directory.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from functools import cache
from pathlib import Path
from typing import Any

TABLE = "piolab"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    pack: str | None = None
    model: str | None = None
    cycles: int = 100_000
    out: Path = Path("build")
    docs: Path | None = None
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The pyproject.toml it came from (None: defaults only).
    source: Path | None = None

    @property
    def root(self) -> Path:
        """The project directory (the pyproject's), else the working directory."""
        return self.source.parent if self.source else Path.cwd()


KEYS = tuple(f.name for f in fields(Config) if f.name != "source")


def find_pyproject(start: Path | None = None) -> Path | None:
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        p = d / "pyproject.toml"
        if p.is_file():
            return p
    return None


def parse(table: dict[str, Any], source: Path | None = None) -> Config:
    unknown = sorted(set(table) - set(KEYS))
    if unknown:
        where = f" in {source}" if source else ""
        raise ConfigError(f"unknown [tool.{TABLE}] key(s) {', '.join(unknown)}{where} "
                          f"(valid: {', '.join(KEYS)})")
    root = source.parent if source else Path.cwd()

    def path(v: Any) -> Path:
        p = Path(str(v))
        return p if p.is_absolute() else root / p

    devices = table.get("devices", {})
    if not isinstance(devices, dict) or not all(isinstance(v, dict) for v in devices.values()):
        raise ConfigError(f"[tool.{TABLE}.devices] maps a device name to a table of options")
    cycles = table.get("cycles", Config.cycles)
    if not isinstance(cycles, int) or cycles <= 0:
        raise ConfigError(f"[tool.{TABLE}] cycles must be a positive integer, not {cycles!r}")
    return Config(
        pack=table.get("pack"),
        model=table.get("model"),
        cycles=cycles,
        out=path(table.get("out", "build")),
        docs=path(table["docs"]) if "docs" in table else None,
        devices={str(k): dict(v) for k, v in devices.items()},
        source=source,
    )


def read(pyproject: Path) -> Config:
    data = tomllib.loads(pyproject.read_text())
    return parse(data.get("tool", {}).get(TABLE, {}), pyproject)


@cache
def _load(cwd: Path) -> Config:
    p = find_pyproject(cwd)
    return read(p) if p else Config()


def load() -> Config:
    """The configuration for the working directory (cached per directory)."""
    return _load(Path.cwd().resolve())


def clear_cache() -> None:
    _load.cache_clear()


__all__ = ["KEYS", "TABLE", "Config", "ConfigError", "clear_cache", "find_pyproject", "load",
           "parse", "read"]

"""PIOScope pre-RTL tooling: assembler, bench, engine models, debugger, device models,
decoders and the architecture study, for any ISA pack (pioscope.packs, ADR-0012)."""

from pathlib import Path
from typing import Any


def __getattr__(name: str) -> Any:
    if name == "PROGRAMS_DIR":
        #: The active ISA pack's protocol programs.
        from pioscope.packs import active

        return active().programs
    raise AttributeError(name)


__all__ = ["PROGRAMS_DIR", "Path"]

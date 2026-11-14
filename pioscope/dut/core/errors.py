"""Engine errors."""

from __future__ import annotations


class EngineError(Exception):
    """A program did something the engine cannot do (with its source location)."""

    def __init__(self, msg: str, src: str = "") -> None:
        super().__init__(f"{src}: {msg}" if src else msg)
        self.src = src


class EngineUnsupported(EngineError):
    """A feature the ISA names but does not yet specify well enough to model (a finding)."""

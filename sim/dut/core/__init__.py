"""The generic engine core every ISA pack's model builds on (ADR-0012).

`Engine` implements the DUT contract over `cores` (cores) and `shared` units;
`Core` holds the registers, FIFOs, pins, side-set, delay, wrap, clock divider,
handler dispatch and the observability contract (ADR-0010); `Unit` is the interface
every unit implements so the tools can see it.
"""

from .config import SYSCLK_HZ, CoreConfig, Role
from .core import M32, Core, Handler, mask, registry, short_src
from .engine import Engine
from .errors import EngineError, EngineUnsupported
from .fifo import Fifo
from .sync import InputSync
from .unit import Unit

__all__ = [
    "M32",
    "SYSCLK_HZ",
    "Core",
    "CoreConfig",
    "Engine",
    "EngineError",
    "EngineUnsupported",
    "Fifo",
    "Handler",
    "InputSync",
    "Role",
    "Unit",
    "mask",
    "registry",
    "short_src",
]

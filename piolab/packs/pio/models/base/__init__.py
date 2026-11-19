"""The `base` model of the PIO pack: one RP2040 state machine on the generic core.

A pack's models live in `<pkg>/models/<name>/`, each exporting `Engine` (the DUT
factory) and `HANDLERS` (one handler per instruction, checked by `piolab isa-check`).
Add a variant by adding a sibling folder; `piolab sim prog -m <name>` picks it.
"""

from .engine import Gpio, IrqFlags, PioConfig, PioEngine, PioSm
from .exec import HANDLERS

#: The name every model package exports (`discover_models`).
Engine = PioEngine

__all__ = ["HANDLERS", "Engine", "Gpio", "IrqFlags", "PioConfig", "PioEngine", "PioSm"]

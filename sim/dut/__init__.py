"""DUT slot: the contract and the stand-ins."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from .base import Dut
from .stub import StubDut, TtTemplateDut

DutFactory = Callable[[], Dut]

#: The DUT names that build an ISA pack's engine model.
ENGINE = "engine"


def engine_factory(image: Any = None) -> DutFactory:
    """The engine model of `image`'s ISA pack (else the active pack's). Local import:
    models import the bench."""
    from sim.packs import for_image

    fn: DutFactory = for_image(image).model_factory()
    return fn


BUILTIN: dict[str, DutFactory] = {"stub": StubDut, "tt_template": TtTemplateDut,
                                  ENGINE: lambda: engine_factory()()}


def dut_factory(spec: str, image: Any = None) -> DutFactory:
    """'stub', 'tt_template', 'engine' (the engine model of `image`'s ISA pack, else the
    active pack's), or 'package.module:callable' for a dropped-in model."""
    if spec == ENGINE:
        return engine_factory(image)
    if spec in BUILTIN:
        return BUILTIN[spec]
    mod, sep, attr = spec.partition(":")
    if not sep:
        raise ValueError(f"unknown DUT '{spec}' (use one of {list(BUILTIN)} or module:callable)")
    fn: DutFactory = getattr(importlib.import_module(mod), attr)
    return fn


__all__ = ["BUILTIN", "ENGINE", "Dut", "DutFactory", "StubDut", "TtTemplateDut", "dut_factory",
           "engine_factory"]

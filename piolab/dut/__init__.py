"""DUT slot: the contract and the stand-ins."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from .base import Dut
from .stub import StubDut, TtTemplateDut

DutFactory = Callable[[], Dut]

#: The DUT name that builds an ISA pack's engine model.
ENGINE = "engine"


def engine_factory(image: Any = None, model: str | None = None) -> DutFactory:
    """Engine model `model` (default: the project's, else the pack's default) of `image`'s
    ISA pack, else of the active pack. Local import: models import the bench."""
    from piolab.packs import for_image

    fn: DutFactory = for_image(image).model_factory(model)
    return fn


BUILTIN: dict[str, DutFactory] = {"stub": StubDut, "tt_template": TtTemplateDut,
                                  ENGINE: lambda: engine_factory()()}


def dut_factory(spec: str | None, image: Any = None, model: str | None = None) -> DutFactory:
    """The DUT for `spec`:

    - 'engine' (or None): the engine model `model` of `image`'s ISA pack (else the active
      pack's); `model` None is the project's `[tool.piolab] model`, else the pack default;
    - 'stub', 'tt_template': the stand-ins;
    - a model name of the pack ('base', ...): that model;
    - 'package.module:callable': a dropped-in model.
    """
    if spec is None or spec == ENGINE:
        return engine_factory(image, model)
    if spec in BUILTIN:
        return BUILTIN[spec]
    mod, sep, attr = spec.partition(":")
    if not sep:
        from piolab.packs import for_image

        pack = for_image(image)
        if spec in pack.models:
            return engine_factory(image, spec)
        raise ValueError(f"unknown DUT '{spec}' (use {', '.join(BUILTIN)}, a model of the "
                         f"'{pack.name}' pack ({', '.join(pack.models)}) or module:callable)")
    fn: DutFactory = getattr(importlib.import_module(mod), attr)
    return fn


__all__ = ["BUILTIN", "ENGINE", "Dut", "DutFactory", "StubDut", "TtTemplateDut", "dut_factory",
           "engine_factory"]

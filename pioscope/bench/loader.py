"""Getting a ProgramImage into the DUT (ADR-0004: abstract loader, backdoor first)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pioscope.dut.base import Dut
from pioscope.image import ProgramImage


class Loader(Protocol):
    def load(self, target: object, image: ProgramImage) -> None: ...


class PyImageLoader:
    """Python DUTs take the image object directly."""

    def load(self, target: object, image: ProgramImage) -> None:
        assert isinstance(target, Dut), f"{target!r} does not implement the Dut contract"
        target.load(image)


class MemFileLoader:
    """RTL backdoor: write prog.hex / cfg.json and hand the simulator plusargs.

    tb.v reads `+PROG=<file>` with $readmemh into the instruction memory once the
    engine RTL exposes one (`PIOSCOPE_IMEM_PATH`); `+CFG=` is reserved until the config
    register map exists.
    """

    def __init__(self, out_dir: Path | str, word_bits: int | None = None) -> None:
        self.out_dir = Path(out_dir)
        self.word_bits = word_bits
        self.plusargs: list[str] = []

    def load(self, target: object, image: ProgramImage) -> None:
        files = image.write_mem(self.out_dir, self.word_bits)
        self.plusargs = [f"+PROG={files['prog'].resolve()}", f"+CFG={files['cfg'].resolve()}"]

"""Stand-in DUTs. Neither one models the engine."""

from __future__ import annotations

from collections.abc import Mapping

from pioscope.bench.pads import PadIn, PadOut
from pioscope.image import ProgramImage


class StubDut:
    """Empty stub for the engine: all outputs low, all uio released, image ignored.

    It exists so that the bench, loader, devices and decoders can be exercised
    before an engine model or RTL exists. Scenarios that need a real engine
    skip on it (`is_engine = False`).
    """

    is_engine = False

    def __init__(self) -> None:
        self.image: ProgramImage | None = None
        self.pushed: list[tuple[int, int]] = []

    def load(self, image: ProgramImage) -> None:
        self.image = image

    def reset(self) -> PadOut:
        return PadOut()

    def step(self, pins_in: PadIn) -> PadOut:
        return PadOut()

    def host_push(self, tc: int, word: int) -> bool:
        self.pushed.append((tc, word))
        return True

    def host_pop(self, tc: int) -> int | None:
        return None

    def host_irq(self) -> int:
        return 0

    def probes(self) -> Mapping[str, int]:
        return {}


class TtTemplateDut(StubDut):
    """Python twin of the Tiny Tapeout template `src/project.v`
    (``uo_out = ui_in + uio_in``, uio released). Used by the backend parity test:
    the same scenario must give identical waveforms on this and on the RTL."""

    def __init__(self) -> None:
        super().__init__()
        self._out = PadOut()

    def reset(self) -> PadOut:
        self._out = PadOut()
        return self._out

    def step(self, pins_in: PadIn) -> PadOut:
        x = pins_in.ui_x | pins_in.uio_x
        self._out = PadOut(uo=(pins_in.ui + pins_in.uio_in) & 0xFF, uo_x=0xFF if x else 0)
        return self._out

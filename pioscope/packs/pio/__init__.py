"""The RP2040 PIO ISA pack: pioasm syntax, the exact RP2040 16-bit encoding, and a
cycle-accurate state-machine model (pioscope/dut/pio). See spec/isa/pio.yaml."""

from pathlib import Path

from pioscope.packs import ROOT, IsaPack

HERE = Path(__file__).resolve().parent

PACK = IsaPack(
    name="pio",
    title="RP2040 PIO",
    spec=ROOT / "spec" / "isa" / "pio.yaml",
    programs=HERE / "programs",
    arch=ROOT / "spec" / "arch" / "pio",
    docs=ROOT / "docs" / "pio",
    model="pioscope.dut.pio:PioEngine",
    demand="pioscope.packs.pio.demand",
    ext=".pio",
    rank=10,
)

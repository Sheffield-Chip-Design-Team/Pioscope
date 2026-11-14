"""The RP2040 PIO ISA pack: pioasm syntax, the exact RP2040 16-bit encoding, and a
cycle-accurate state-machine model (sim/dut/pio). See spec/isa/pio.yaml."""

from pathlib import Path

from sim.packs import ROOT, IsaPack

HERE = Path(__file__).resolve().parent

PACK = IsaPack(
    name="pio",
    title="RP2040 PIO",
    spec=ROOT / "spec" / "isa" / "pio.yaml",
    programs=HERE / "programs",
    arch=ROOT / "spec" / "arch" / "pio",
    docs=ROOT / "docs" / "pio",
    model="sim.dut.pio:PioEngine",
    demand="sim.packs.pio.demand",
    ext=".pio",
    rank=10,
)

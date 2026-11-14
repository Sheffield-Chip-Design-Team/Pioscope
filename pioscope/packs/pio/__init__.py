"""The RP2040 PIO ISA pack: pioasm syntax, the exact RP2040 16-bit encoding, and a
cycle-accurate state-machine model (pioscope/dut/pio). The spec is isa.yaml here."""

from pioscope.packs import IsaPack, pack_dir

HERE = pack_dir(__file__)

PACK = IsaPack(
    name="pio",
    title="RP2040 PIO",
    spec=HERE / "isa.yaml",
    programs=HERE / "programs",
    arch=HERE / "arch",
    model="pioscope.dut.pio:PioEngine",
    demand="pioscope.packs.pio.demand",
    ext=".pio",
    rank=10,
)

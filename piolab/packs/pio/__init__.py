"""The RP2040 PIO ISA pack: the demonstration pack that ships with PIOLab.

It is a complete, worked example of an ISA pack laid out the way a project's own pack
is (`piolab new-pack NAME` scaffolds the same shape):

- `isa.yaml`: pioasm syntax and the exact RP2040 16-bit encoding (the single source of
  truth), with its hooks `directives.py` and `encode.py`;
- `models/<name>/`: the engine models, one folder each (`base`: one state machine);
- `programs/`: UART, SPI, I²C and WS2812 programs; `arch/` + `demand.py`: the arch study;
- `tests/`: directed tests, scenarios and the pack checks.

Its rank is high, so an installed project pack is the default; `--isa pio` selects it.
"""

from piolab.packs import IsaPack, discover_models, pack_dir

HERE = pack_dir(__file__)

PACK = IsaPack(
    name="pio",
    title="RP2040 PIO",
    spec=HERE / "isa.yaml",
    programs=HERE / "programs",
    arch=HERE / "arch",
    models=discover_models("piolab.packs.pio.models"),
    demand="piolab.packs.pio.demand",
    scenarios="piolab.packs.pio.tests.scenarios",
    ext=".pio",
    rank=1000,
)

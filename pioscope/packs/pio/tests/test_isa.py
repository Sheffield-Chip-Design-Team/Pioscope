"""The PIO spec: it loads, every entry is implemented, and the generated docs are current."""

import pytest

from pioscope.isa import default_isa
from pioscope.isa.check import Check, check_pack
from pioscope.packs import get

PACK = get("pio")


def test_spec_loads_with_the_nine_pio_instructions() -> None:
    isa = default_isa()
    assert isa.pack == "pio" and isa.encoding.word_bits == 16
    assert set(isa.instructions) == {"jmp", "wait", "in", "out", "push", "pull", "mov", "irq",
                                     "set", "nop"}


@pytest.mark.parametrize("check", check_pack(PACK), ids=lambda c: c.name)
def test_isa_check(check: Check) -> None:
    """Handlers, directives, semantics text, programs, usage, generated docs
    (`pioscope --isa pio isa-check`)."""
    assert check.ok, check.report()

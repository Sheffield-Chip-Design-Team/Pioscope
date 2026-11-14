"""The PIO spec: it loads, every entry is implemented, and the generated docs are current."""

import re

from sim.asm.directives import handlers
from sim.isa import default_isa
from sim.packs import get

PACK = get("pio")


def test_spec_loads_with_the_nine_pio_instructions() -> None:
    isa = default_isa()
    assert isa.pack == "pio" and isa.encoding.word_bits == 16
    assert set(isa.instructions) == {"jmp", "wait", "in", "out", "push", "pull", "mov", "irq",
                                     "set", "nop"}


def test_every_directive_has_a_handler() -> None:
    isa = default_isa()
    assert set(isa.directives) <= set(handlers(isa))


def test_every_instruction_is_used_by_a_program_or_test() -> None:
    isa = default_isa()
    corpus = "\n".join(p.read_text() for p in PACK.programs.glob("*.pio"))
    corpus += (PACK.programs.parent / "tests" / "test_exec.py").read_text()
    missing = [n for n in isa.instructions if not re.search(rf"\b{re.escape(n)}\b", corpus)]
    assert not missing


def test_generated_docs_are_current() -> None:
    from sim.scripts import gen_isa_docs, gen_isa_encoding, gen_isa_html

    isa = default_isa()
    for mod in (gen_isa_docs, gen_isa_encoding, gen_isa_html):
        assert mod.out_path().read_text() == mod.render(isa), (
            f"{mod.out_path()} is stale: run `PIOSCOPE_ISA=pio uv run pioscope isa-docs`")

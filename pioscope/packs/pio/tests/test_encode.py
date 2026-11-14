"""The RP2040 16-bit encoding: programs against pioasm's output, every instruction form
against the datasheet layout, and decode/encode round trips."""

import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pioscope.asm import AsmErrors, assemble, assemble_file
from pioscope.asm.encode import EncodeError
from pioscope.isa import load_isa
from pioscope.packs import get
from pioscope.packs.pio.encode import decode_word, delay_max, disassemble, encode_instr

PACK = get("pio")
ISA = load_isa(PACK.spec)

#: pioasm's output for the same programs (the Pico SDK's generated headers).
PIOASM = {
    "uart_tx": [0x9FA0, 0xF727, 0x6001, 0x0642],
    "uart_rx": [0x2020, 0xEA27, 0x4001, 0x0642, 0x00C8, 0xC014, 0x20A0, 0x0000, 0x8020],
    "spi_m0": [0x6101, 0x5101],
    "spi_m1": [0x6021, 0xB101, 0x4001],
    "ws2812": [0x6221, 0x1123, 0x1400, 0xA442],
}


@pytest.mark.parametrize("name", sorted(PIOASM))
def test_programs_match_pioasm(name: str) -> None:
    assert assemble_file(PACK.program(name)).words == PIOASM[name]


#: One line per instruction form, with its word from the datasheet's field tables.
FORMS = [
    ("jmp 0", 0x0000), ("jmp !x 5", 0x0025), ("jmp x-- 5", 0x0045), ("jmp !y 5", 0x0065),
    ("jmp y-- 5", 0x0085), ("jmp x!=y 5", 0x00A5), ("jmp pin 5", 0x00C5),
    ("jmp !osre 5", 0x00E5),
    ("wait 0 gpio 3", 0x2003), ("wait 1 gpio 3", 0x2083), ("wait 1 pin 2", 0x20A2),
    ("wait 0 irq 3", 0x2043), ("wait 1 irq 3 rel", 0x20D3),
    ("in pins, 32", 0x4000), ("in x, 5", 0x4025), ("in y, 1", 0x4041), ("in null, 8", 0x4068),
    ("in isr, 4", 0x40C4), ("in osr, 2", 0x40E2),
    ("out pins, 1", 0x6001), ("out x, 32", 0x6020), ("out y, 1", 0x6041),
    ("out null, 4", 0x6064), ("out pindirs, 3", 0x6083), ("out pc, 5", 0x60A5),
    ("out isr, 7", 0x60C7), ("out exec, 16", 0x60F0),
    ("push", 0x8020), ("push noblock", 0x8000), ("push iffull", 0x8060),
    ("push iffull noblock", 0x8040), ("pull", 0x80A0), ("pull noblock", 0x8080),
    ("pull ifempty", 0x80E0), ("pull ifempty noblock", 0x80C0),
    ("mov x, y", 0xA022), ("mov y, ~x", 0xA049), ("mov y, !x", 0xA049),
    ("mov isr, ::osr", 0xA0D7), ("mov osr, !null", 0xA0EB), ("mov pc, x", 0xA0A1),
    ("mov exec, x", 0xA081), ("mov pins, isr", 0xA006), ("mov x, status", 0xA025),
    ("nop", 0xA042), ("nop [31]", 0xBF42),
    ("irq 3", 0xC003), ("irq set 3", 0xC003), ("irq nowait 3", 0xC003),
    ("irq wait 3", 0xC023), ("irq clear 3", 0xC043), ("irq 2 rel", 0xC012),
    ("irq wait 1 rel", 0xC031),
    ("set pins, 1", 0xE001), ("set x, 31", 0xE03F), ("set y, 0", 0xE040),
    ("set pindirs, 1", 0xE081),
]


def _one(line: str, header: str = "") -> int:
    im = assemble(f".program t\n{header}\n{line}\n", "t.pio", ISA)
    w = im.words[0]
    assert w is not None
    return w


@pytest.mark.parametrize("line, word", FORMS, ids=[f[0] for f in FORMS])
def test_instruction_forms(line: str, word: int) -> None:
    assert _one(line) == word
    assert decode_word(word, ISA)[0] == line.split()[0]
    assert _one(disassemble(word, ISA)) == word


@pytest.mark.parametrize("header, line, word", [
    (".side_set 2", "nop side 3 [7]", 0xBF42),
    (".side_set 2 opt", "nop side 2 [3]", 0xBB42),
    (".side_set 2 opt", "nop [3]", 0xA342),
    (".side_set 1 pindirs", "set x, 0 side 1", 0xF020),
    (".side_set 5", "jmp 0 side 31", 0x1F00),
])
def test_side_set_shares_the_delay_field(header: str, line: str, word: int) -> None:
    assert _one(line, header) == word


@pytest.mark.parametrize("header, most", [("", 31), (".side_set 1", 15),
                                          (".side_set 1 opt", 7), (".side_set 4 opt", 0),
                                          (".side_set 5", 0)])
def test_delay_max_follows_side_set(header: str, most: int) -> None:
    side = " side 0" if header else ""
    im = assemble(f".program t\n{header}\nnop{side}\n", "t.pio", ISA)
    assert delay_max(ISA, im.config) == most
    with pytest.raises(AsmErrors, match="bad delay"):
        assemble(f".program t\n{header}\nnop{side} [{most + 1}]\n", "t.pio", ISA)


def test_wait_irq_index_and_rel_checks() -> None:
    with pytest.raises(AsmErrors, match=r"not 0\.\.7"):
        _one("wait 1 irq 9")
    with pytest.raises(AsmErrors, match="wait irq only"):
        _one("wait 1 gpio 2 rel")


_SIDES = [(0, False), (1, False), (1, True), (2, True), (3, False)]


@given(st.integers(0, 0xFFFF), st.sampled_from(_SIDES))
def test_decode_encode_round_trip(word: int, side: tuple[int, bool]) -> None:
    count, opt = side
    config = {"side_set": {"count": count, "opt": opt, "pindirs": False}} if count else {}
    try:
        name, ops, sv, delay = decode_word(word, ISA, 32, config)
    except EncodeError:
        return  # a reserved encoding
    if word >> 13 == 6 and word & 0x60 == 0x60:
        return  # irq with clear and wait: clear wins on an RP2040; not a canonical form
    from pioscope.image import IrInstr

    ins = IrInstr(addr=0, name=name, operands=ops, side=sv, delay=delay)
    again = encode_instr(ins, ISA, config)
    if opt and sv is None:  # side-set disabled: its value bits are don't-care
        dbits = 5 - count - 1
        care = 0xFFFF & ~(((1 << count) - 1) << (8 + dbits))
        assert again & care == word & care
    else:
        assert again == word


@pytest.mark.skipif(shutil.which("pioasm") is None, reason="pioasm is not installed")
@pytest.mark.parametrize("name", sorted(PIOASM))
def test_against_installed_pioasm(name: str, tmp_path: Path) -> None:
    """Optional: compare with a local pioasm (Pico SDK). The harness directives are
    stripped, since pioasm does not know them."""
    src = PACK.program(name).read_text().splitlines()
    keep = [ln for ln in src if not ln.strip().startswith((".pins", ".sm_config", ".clkdiv"))]
    f = tmp_path / f"{name}.pio"
    f.write_text("\n".join(keep) + "\n")
    out = subprocess.run(["pioasm", "-o", "hex", str(f)], capture_output=True, text=True,
                         check=True).stdout
    assert [int(x, 16) for x in out.split()] == assemble_file(PACK.program(name)).words

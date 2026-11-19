"""Directed tests of the PIO model, one or more per instruction (agreement #6), against
the RP2040 datasheet's semantics (piolab/packs/pio/isa.yaml)."""

from __future__ import annotations

import pytest

from piolab.asm import assemble
from piolab.bench.pads import PadIn
from piolab.isa import load_isa
from piolab.packs import get
from piolab.packs.pio.models.base import PioEngine, PioSm

ISA = load_isa(get("pio").spec)
PINS = ".pins a=uio0 b=uio1 c=uio2 out=uo0 in0=ui0 in1=ui1\n"


def engine(body: str, header: str = "") -> PioEngine:
    e = PioEngine()
    e.load(assemble(f".program t\n{PINS}{header}\n{body}\n", "t.pio", ISA))
    e.reset()
    return e


def sm(e: PioEngine) -> PioSm:
    t = e.cores[0]
    assert isinstance(t, PioSm)
    return t


def run(e: PioEngine, cycles: int, ui: int = 0, uio: int = 0) -> None:
    for _ in range(cycles):
        e.step(PadIn(ui=ui, uio_in=uio))


# ---- jmp ------------------------------------------------------------------------------


@pytest.mark.parametrize("cond, x, y, taken", [
    ("!x", 0, 0, True), ("!x", 1, 0, False), ("!y", 0, 0, True), ("!y", 0, 2, False),
    ("x!=y", 1, 2, True), ("x!=y", 3, 3, False)])
def test_jmp_conditions(cond: str, x: int, y: int, taken: bool) -> None:
    e = engine(f"jmp {cond} yes\nno: jmp no\nyes: jmp yes")
    sm(e).x, sm(e).y = x, y
    run(e, 3)
    assert sm(e).labels[sm(e).pc] == ("yes" if taken else "no")


def test_jmp_x_decrement_runs_n_plus_one_times() -> None:
    e = engine("set x, 3\nloop: set y, 0\njmp x-- loop\nend: jmp end")
    run(e, 1 + 4 * 2)
    assert sm(e).labels[sm(e).pc] == "end"
    assert sm(e).x == 0xFFFF_FFFF  # decremented past zero on the last, untaken test


def test_jmp_y_decrement() -> None:
    e = engine("set y, 1\nloop: jmp y-- loop\nend: jmp end")
    run(e, 4)
    assert sm(e).labels[sm(e).pc] == "end"


def test_jmp_pin_uses_the_jmp_pin() -> None:
    e = engine("jmp pin hi\nlo: jmp lo\nhi: jmp hi", ".sm_config jmp=in1")
    run(e, 4, ui=0b10)  # 2 cycles of input synchroniser, then the jmp
    e2 = engine("nop\nnop\njmp pin hi\nlo: jmp lo\nhi: jmp hi", ".sm_config jmp=in1")
    run(e2, 5, ui=0b10)
    assert sm(e2).labels[sm(e2).pc] == "hi"


def test_jmp_not_osre_compares_with_the_pull_threshold() -> None:
    e = engine("pull\nout null, 4\njmp !osre more\nempty: jmp empty\nmore: jmp more",
               ".sm_config pull_thresh=8")
    e.host_push(0, 0xFF)
    run(e, 4)
    assert sm(e).labels[sm(e).pc] == "more"
    e = engine("pull\nout null, 8\njmp !osre more\nempty: jmp empty\nmore: jmp more",
               ".sm_config pull_thresh=8")
    e.host_push(0, 0xFF)
    run(e, 4)
    assert sm(e).labels[sm(e).pc] == "empty"


def test_jmp_numeric_target() -> None:
    e = engine("nop\njmp 0")
    run(e, 2)
    assert sm(e).pc == 0


# ---- wait -----------------------------------------------------------------------------


def test_wait_gpio_and_pin() -> None:
    # two nops let the input synchroniser (reset to 1s) fill with the real levels
    e = engine("nop\nnop\nw: wait 1 gpio 1\ndone: jmp done")
    run(e, 5)
    assert sm(e).labels[sm(e).pc] == "w" and sm(e).stall.startswith("wait")
    run(e, 3, ui=0b10)
    assert sm(e).labels[sm(e).pc] == "done"
    e = engine("nop\nnop\nw: wait 0 pin 1\ndone: jmp done", ".sm_config in=in0")
    run(e, 5, ui=0b11)
    assert sm(e).labels[sm(e).pc] == "w"
    run(e, 3, ui=0b01)
    assert sm(e).labels[sm(e).pc] == "done"


def test_wait_irq_clears_the_flag_when_satisfied() -> None:
    e = engine("wait 1 irq 2\ndone: jmp done")
    run(e, 3)
    assert sm(e).pc == 0
    e.irqs.flags = 0b100
    run(e, 1)
    assert sm(e).labels[sm(e).pc] == "done" and e.irqs.flags == 0


def test_wait_0_irq_waits_for_clear() -> None:
    e = engine("irq 5\nwait 0 irq 5\ndone: jmp done")
    run(e, 4)
    assert sm(e).pc == 1
    e.irqs.flags = 0
    run(e, 1)
    assert sm(e).labels[sm(e).pc] == "done"


# ---- in / out ------------------------------------------------------------------------


def test_in_shifts_right_by_default_and_left_when_configured() -> None:
    e = engine("set x, 5\nin x, 3\nin x, 3\nend: jmp end")
    run(e, 3)
    assert sm(e).isr == (5 << 29) >> 3 | (5 << 29) and sm(e).isr_count == 6
    e = engine("set x, 5\nin x, 3\nin x, 3\nend: jmp end", ".sm_config in_shift=left")
    run(e, 3)
    assert sm(e).isr == 0b101101 and sm(e).isr_count == 6


def test_in_pins_reads_from_the_in_base() -> None:
    e = engine("nop\nnop\nin pins, 2\nend: jmp end", ".sm_config in=in0 in_shift=left")
    run(e, 3, ui=0b10)
    assert sm(e).isr == 0b10


def test_autopush_pushes_at_the_threshold_and_stalls_when_full() -> None:
    e = engine("loop: in x, 4\njmp loop", ".sm_config autopush=8 in_shift=left\n.fifo_dummy"
               .replace("\n.fifo_dummy", ""))
    run(e, 4)
    assert len(sm(e).rxf) == 1 and sm(e).isr_count == 0
    run(e, 40)
    assert len(sm(e).rxf) == 4 and sm(e).stall == "autopush: rx fifo full"


def test_out_shifts_and_counts() -> None:
    e = engine("pull\nout x, 4\nout y, 4\nend: jmp end")
    e.host_push(0, 0xA5)
    run(e, 3)
    assert (sm(e).x, sm(e).y, sm(e).osr_count) == (0x5, 0xA, 8)
    e = engine("pull\nout x, 4\nend: jmp end", ".sm_config out_shift=left")
    e.host_push(0, 0xA000_0000)
    run(e, 2)
    assert sm(e).x == 0xA


def test_out_pins_writes_the_out_group_and_pindirs() -> None:
    e = engine("set x, 0b101\nmov osr, x\nout pins, 3\nend: jmp end", ".sm_config out=a:3")
    run(e, 3)
    assert e.gpio.out >> 16 & 0b111 == 0b101
    e = engine("set x, 0b010\nmov osr, x\nout pindirs, 3\nend: jmp end", ".sm_config out=a:3")
    run(e, 3)
    assert e.gpio.oe >> 16 & 0b111 == 0b010


def test_out_pc_and_out_isr() -> None:
    e = engine("set x, 3\nmov osr, x\nout pc, 5\nnop\nend: jmp end")
    run(e, 3)
    assert sm(e).pc == 3
    e = engine("set x, 7\nmov osr, x\nout isr, 3\nend: jmp end")
    run(e, 3)
    assert (sm(e).isr, sm(e).isr_count) == (7, 3)


def test_out_exec_runs_the_word_without_moving_the_pc() -> None:
    e = engine("pull\nout exec, 16\nend: jmp end")
    e.host_push(0, 0xE03A)  # set x, 26
    run(e, 3)
    assert sm(e).x == 26 and sm(e).labels.get(sm(e).pc) == "end"


def test_autopull_refills_and_stalls_when_empty() -> None:
    e = engine("loop: out x, 8\njmp loop", ".sm_config autopull=8")
    e.host_push(0, 0x11)
    e.host_push(0, 0x22)
    run(e, 2)
    assert sm(e).x == 0x11
    run(e, 2)
    assert sm(e).x == 0x22
    run(e, 3)
    assert sm(e).stall == "autopull: tx fifo empty"


# ---- push / pull ---------------------------------------------------------------------


def test_push_block_iffull_and_noblock() -> None:
    e = engine("in x, 4\npush iffull\nend: jmp end", ".sm_config push_thresh=8")
    run(e, 3)
    assert len(sm(e).rxf) == 0 and sm(e).isr_count == 4
    e = engine("loop: push noblock\njmp loop")
    run(e, 20)
    assert len(sm(e).rxf) == 4 and sm(e).stall == ""
    e = engine("loop: push\njmp loop")
    run(e, 20)
    assert sm(e).stall == "rx fifo full"


def test_pull_noblock_copies_x_and_ifempty() -> None:
    e = engine("set x, 9\npull noblock\nend: jmp end")
    run(e, 2)
    assert (sm(e).osr, sm(e).osr_count) == (9, 0)
    e = engine("pull\npull ifempty\nend: jmp end")
    e.host_push(0, 1)
    e.host_push(0, 2)
    run(e, 2)
    assert sm(e).osr == 1  # OSR is full, so pull ifempty does nothing
    e = engine("pull\nend: jmp end")
    run(e, 3)
    assert sm(e).stall == "tx fifo empty"


# ---- mov / nop -------------------------------------------------------------------------


def test_mov_ops_and_status() -> None:
    e = engine("set x, 1\nmov y, ::x\nmov x, ~x\nend: jmp end")
    run(e, 3)
    assert (sm(e).x, sm(e).y) == (0xFFFF_FFFE, 0x8000_0000)
    e = engine("mov x, status\nend: jmp end", ".sm_config status=tx<1")
    run(e, 1)
    assert sm(e).x == 0xFFFF_FFFF  # the TX FIFO is empty


def test_mov_isr_osr_reset_counts_and_mov_pc_exec() -> None:
    e = engine("set x, 3\nmov isr, x\nmov osr, x\nend: jmp end")
    run(e, 3)
    assert (sm(e).isr_count, sm(e).osr_count) == (0, 0)
    e = engine("set x, 3\nmov pc, x\nnop\nend: jmp end")
    run(e, 2)
    assert sm(e).pc == 3
    e = engine("pull\nmov exec, osr\nend: jmp end")
    e.host_push(0, 0xE05F)  # set y, 31
    run(e, 3)
    assert sm(e).y == 31


def test_nop_side_set_and_delay() -> None:
    e = engine("nop side 1 [3]\nnop side 0\nend: jmp end side 0",
               ".side_set 1\n.sm_config sideset=out")
    run(e, 1)
    assert e.gpio.out >> 8 & 1 == 1
    run(e, 3)
    assert sm(e).pc == 1  # 3 delay cycles
    run(e, 1)
    assert e.gpio.out >> 8 & 1 == 0


# ---- irq / set -------------------------------------------------------------------------


def test_irq_set_wait_clear_and_rel() -> None:
    e = engine("irq 1\nirq clear 1\nirq 2 rel\nend: jmp end")
    run(e, 1)
    assert e.irqs.flags == 0b10 and e.host_irq() == 0b10
    run(e, 2)
    assert e.irqs.flags == 0b100  # SM0: rel adds 0
    e = engine("irq wait 3\nend: jmp end")
    run(e, 4)
    assert sm(e).pc == 0 and sm(e).stall == "irq 3 set"
    e.irqs.flags = 0
    run(e, 1)
    assert sm(e).labels[sm(e).pc] == "end"
    assert sm(e).irq_index(3, True) == 3 and sm(e).irq_index(6, True) == 6


def test_set_pins_pindirs_and_registers() -> None:
    e = engine("set pins, 0b11\nset pindirs, 0b01\nset x, 31\nset y, 1\nend: jmp end",
               ".sm_config set=a:2")
    run(e, 4)
    assert e.gpio.out >> 16 & 3 == 3 and e.gpio.oe >> 16 & 3 == 1
    assert (sm(e).x, sm(e).y) == (31, 1)


def test_side_set_wins_over_set_on_the_same_pin() -> None:
    e = engine("set pins, 1 side 0\nend: jmp end side 0", ".side_set 1\n.sm_config set=a sideset=a")
    run(e, 1)
    assert e.gpio.out >> 16 & 1 == 0


def test_side_set_pindirs_and_opt() -> None:
    e = engine("nop side 1\nnop\nend: jmp end", ".side_set 1 opt pindirs\n.sm_config sideset=a")
    e.gpio.oe = 0
    run(e, 1)
    assert e.gpio.oe >> 16 & 1 == 1
    run(e, 1)
    assert e.gpio.oe >> 16 & 1 == 1  # no side-set: unchanged


def test_wrap_and_clock_divider() -> None:
    e = engine("set x, 1\n.wrap_target\nset y, 1\nset y, 2\n.wrap\nend: jmp end",
               ".clkdiv 2")
    run(e, 2 * 4)
    assert sm(e).issued == 4 and sm(e).pc == 2  # issued 0, 1, 2, then 1 (wrapped)


def test_joined_fifo() -> None:
    e = engine("end: jmp end", ".sm_config fifo=tx")
    assert sm(e).txf.depth == 8 and sm(e).rxf.depth == 0

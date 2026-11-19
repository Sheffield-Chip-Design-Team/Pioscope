"""The architecture study on the PIO pack: no units, so demand is cores, imem and FIFOs."""

from piolab.arch.arch import load_archs
from piolab.arch.cost import load_cost
from piolab.arch.demand import load_workloads, program_demand, unit_types
from piolab.arch.export import study_data
from piolab.arch.report import out_path, render
from piolab.arch.viz import out_path as page_path
from piolab.arch.viz import render as render_page


def test_pio_has_no_unit_types() -> None:
    assert unit_types() == ()


def test_program_demand() -> None:
    d = program_demand("uart_tx")
    assert (d.imem_words, d.units, d.fifo_tx > 0, d.fifo_rx > 0) == (4, frozenset(), True, False)
    d = program_demand("spi_m0")  # autopull + autopush: both FIFOs, no explicit pull/push
    assert (d.imem_words, d.fifo_tx > 0, d.fifo_rx > 0) == (2, True, True)
    assert program_demand("uart_rx").fifo_tx == 0


def test_which_workloads_fit_the_rp2040_block() -> None:
    """32 shared words hold every mix but io_hub: its four programs need 37 (the I2C
    writer alone is 22). A finding of the study, kept as a test."""
    from piolab.arch.evaluate import score

    ws, archs, cost = load_workloads(), load_archs(), load_cost()
    rp = next(a for a in archs if a.name == "rp2040_block")
    reasons = {w.name: score(rp, w, cost).reason for w in ws}
    assert reasons.pop("io_hub") == "programs total 37 words; shared imem is 32"
    assert not any(reasons.values()), reasons


def test_generated_study_is_current() -> None:
    ws, archs, cost = load_workloads(), load_archs(), load_cost()
    assert out_path().read_text() == render(ws, archs, cost), (
        "docs/pio/arch-study.md is stale: run `PIOLAB_ISA=pio uv run piolab arch --write`")
    assert page_path().read_text() == render_page(study_data(ws, archs, cost)), (
        "docs/pio/arch-study.html is stale: run `PIOLAB_ISA=pio uv run piolab arch --write`")

"""The drop-in guarantee: the same scenario on the Python twin of the TT
template and on the template RTL (piolab/bench/rtl/tt_template.v) gives identical waveforms.

Runs on the simulator selected with --sim (default icarus); skipped if that
simulator is not installed.
"""

import shutil

import pytest

from piolab.bench.backends.cocotb_rtl import run_rtl
from piolab.bench.backends.python import run_python
from piolab.bench.parity import first_difference, parity_async_scenario, parity_scenario
from piolab.dut import TtTemplateDut


@pytest.mark.rtl
@pytest.mark.parametrize("factory", [parity_scenario, parity_async_scenario],
                         ids=["sync", "async"])
def test_python_and_rtl_waveforms_identical(request: pytest.FixtureRequest, factory) -> None:  # type: ignore[no-untyped-def]
    sim = request.config.getoption("--sim")
    if shutil.which("iverilog" if sim == "icarus" else "verilator") is None:
        pytest.skip(f"{sim} not installed")
    py = run_python(factory(), TtTemplateDut())
    rtl = run_rtl(factory, sim=sim, waves=request.config.getoption("--waves"))
    assert first_difference(py.waveform, rtl.waveform) is None
    assert py.waveform.digest() == rtl.waveform.digest()
    assert {k: [t.to_dict() for t in v] for k, v in py.logs.items()} == \
        {k: [t.to_dict() for t in v] for k, v in rtl.logs.items()}
    assert py.stats == rtl.stats

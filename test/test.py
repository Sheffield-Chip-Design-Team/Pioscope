# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles


@cocotb.test()
async def test_project(dut):
    dut._log.info("Start")

    # Set the clock period to 10 us (100 KHz)
    clock = Clock(dut.clk, 10, unit="us")
    cocotb.start_soon(clock.start())

    # Reset
    dut._log.info("Reset")
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1

    dut._log.info("Test project behavior")

    # Set the input values you want to test
    dut.ui_in.value = 20
    dut.uio_in.value = 30

    # Wait for one clock cycle to see the output values
    await ClockCycles(dut.clk, 1)

    # The following assersion is just an example of how to check the output values.
    # Change it to match the actual expected output of your module:
    assert dut.uo_out.value == 50

    # Keep testing the module by changing the input values, waiting for
    # one or more clock cycles, and asserting the expected output values.


# ---------------------------------------------------------------------------------
# PIOScope bench (sim package). Skipped where `sim` is not installed (e.g. the
# Tiny Tapeout CI, which only installs test/requirements.txt). From the repo:
#   uv run make -B          (in test/)
# The full scenario suite runs through pytest instead: `uv run pytest --backend=rtl`.
# ---------------------------------------------------------------------------------
try:
    from pioscope.bench.backends.cocotb_rtl import drive_scenario
    from pioscope.bench.backends.python import run_python
    from pioscope.bench.parity import first_difference, parity_scenario
    from pioscope.dut import TtTemplateDut

    HAVE_PE = True
except ImportError:  # pragma: no cover
    HAVE_PE = False


@cocotb.test(skip=not HAVE_PE or os.environ.get("GATES") == "yes")
async def test_bench_parity_with_python_twin(dut):
    """The template RTL and its Python twin give identical bench waveforms."""
    rtl = await drive_scenario(dut, parity_scenario())
    py = run_python(parity_scenario(), TtTemplateDut())
    diff = first_difference(py.waveform, rtl.waveform)
    assert diff is None, diff

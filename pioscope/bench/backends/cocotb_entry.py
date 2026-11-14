"""cocotb test module loaded by the simulator for `run_rtl`.

Environment:
    PIOSCOPE_SCENARIO       module:factory of the scenario to run
    PIOSCOPE_SCENARIO_ARGS  base64-pickled kwargs for the factory
    PIOSCOPE_RESULT         where to write the Result JSON
    PIOSCOPE_TRACE          non-empty: log every device/host event live
"""

from __future__ import annotations

import os
from typing import Any

import cocotb

from pioscope.bench.backends.cocotb_rtl import decode_kwargs, drive_scenario, load_factory


@cocotb.test()
async def run_scenario(dut: Any) -> None:
    scenario = load_factory(os.environ["PIOSCOPE_SCENARIO"],
                            decode_kwargs(os.environ.get("PIOSCOPE_SCENARIO_ARGS", "")))
    dut._log.info("scenario %s: %d cycles max", scenario.name, scenario.max_cycles)
    result = await drive_scenario(dut, scenario, trace=bool(os.environ.get("PIOSCOPE_TRACE")))
    result.save(os.environ["PIOSCOPE_RESULT"])
    dut._log.info("scenario %s: done after %d cycles", scenario.name, result.cycles)

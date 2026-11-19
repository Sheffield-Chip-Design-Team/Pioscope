"""cocotb test module loaded by the simulator for `run_rtl`.

Environment:
    PIOLAB_SCENARIO       module:factory of the scenario to run
    PIOLAB_SCENARIO_ARGS  base64-pickled kwargs for the factory
    PIOLAB_RESULT         where to write the Result JSON
    PIOLAB_TRACE          non-empty: log every device/host event live
"""

from __future__ import annotations

import os
from typing import Any

import cocotb

from piolab.bench.backends.cocotb_rtl import decode_kwargs, drive_scenario, load_factory


@cocotb.test()
async def run_scenario(dut: Any) -> None:
    scenario = load_factory(os.environ["PIOLAB_SCENARIO"],
                            decode_kwargs(os.environ.get("PIOLAB_SCENARIO_ARGS", "")))
    dut._log.info("scenario %s: %d cycles max", scenario.name, scenario.max_cycles)
    result = await drive_scenario(dut, scenario, trace=bool(os.environ.get("PIOLAB_TRACE")))
    result.save(os.environ["PIOLAB_RESULT"])
    dut._log.info("scenario %s: done after %d cycles", scenario.name, result.cycles)

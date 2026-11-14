"""Backend parity scenario.

Devices drive activity onto DUT input pads (UART on ui0, an SPI master on
ui1..ui3, a pulled-up net on uio0) and listen on DUT outputs (uo0..uo3). With
the Tiny Tapeout template DUT (`uo = ui + uio_in`) the outputs depend on the
inputs, so the run exercises both directions of the adapter. The Python twin
(`TtTemplateDut`) and the RTL (`src/project.v`) must give identical waveforms.
"""

from __future__ import annotations

from pioscope.devices import SpiMaster, UartConfig, UartPartner
from pioscope.wave import Waveform

from .scenario import Attach, Scenario
from .wiring import NetSpec, Wiring


def parity_scenario(cycles: int = 3000) -> Scenario:
    nets = {
        "u_tx": NetSpec(pad="ui0"),
        "sck": NetSpec(pad="ui1"),
        "mosi": NetSpec(pad="ui2"),
        "cs": NetSpec(pad="ui3"),
        "pulled": NetSpec(pad="uio0", pull=1),
        "miso": NetSpec(pull=0),
        "o0": NetSpec(pad="uo0"),
        "o1": NetSpec(pad="uo1"),
        "o2": NetSpec(pad="uo2"),
        "o3": NetSpec(pad="uo3"),
    }
    uart = UartPartner("uart", UartConfig(baud=2_500_000), send=[0x5A, 0xC3, 0x0F])
    mon = UartPartner("mon", UartConfig(baud=2_500_000))
    spi = SpiMaster("spi", [[0xA5, 0x3C]], mode=1, half_period=7)
    return Scenario(
        name="parity",
        wiring=Wiring(nets),
        devices=[
            Attach(uart, {"tx": "u_tx"}),
            Attach(mon, {"rx": "o0"}),
            Attach(spi, {"sck": "sck", "mosi": "mosi", "cs": "cs", "miso": "miso"}),
        ],
        max_cycles=cycles,
        requires_engine=False,
    )


def first_difference(a: Waveform, b: Waveform) -> str | None:
    """Human description of the first differing (net, cycle), or None."""
    if a.length != b.length:
        return f"lengths differ: {a.length} vs {b.length}"
    for net in sorted(set(a.nets) | set(b.nets)):
        if net not in a.changes or net not in b.changes:
            return f"net {net} missing on one side"
        if a.changes[net] != b.changes[net]:
            for (ca, la), (cb, lb) in zip(a.changes[net], b.changes[net], strict=False):
                if (ca, la) != (cb, lb):
                    return f"net {net}: first difference near cycle {min(ca, cb)}"
            return f"net {net}: change lists differ in length"
    return None


def parity_async_scenario(cycles: int = 4000) -> Scenario:
    """Like parity_scenario, but every device runs on its own asynchronous clock
    (non-integer ratios to sysclk, ppm, phase offset, jitter) and the DUT samples
    through a 2 ns setup window. Both backends must still agree exactly."""
    sc = parity_scenario(cycles)
    uart = UartPartner("uart", UartConfig(baud=2_345_678, ppm=1500, offset_s=7.3e-9,
                                          jitter_rms_s=0.4e-9),
                       send=[0x5A, 0xC3, 0x0F, 0x81], seed=11)
    mon = UartPartner("mon", UartConfig(baud=2_345_678))
    spi = SpiMaster("spi", [[0xA5, 0x3C, 0x96]], mode=1, sck_hz=12.34e6, offset_s=3.1e-9,
                    jitter_rms_s=0.3e-9, seed=5)
    return Scenario(
        name="parity_async",
        wiring=sc.wiring,
        devices=[
            Attach(uart, {"tx": "u_tx"}),
            Attach(mon, {"rx": "o0"}),
            Attach(spi, {"sck": "sck", "mosi": "mosi", "cs": "cs", "miso": "miso"}),
        ],
        max_cycles=cycles,
        requires_engine=False,
        sample_aperture_s=2e-9,
        seed=1,
    )

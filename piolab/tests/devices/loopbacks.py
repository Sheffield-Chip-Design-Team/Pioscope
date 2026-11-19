"""Device <-> device loopback scenarios (no engine needed).

Module-level factories so the cocotb backend can rebuild them by import path.
Nets are wired to DUT input pads (ui*) so that, under --backend=rtl, the
traffic also passes through the HDL testbench's ui_in.
"""

from __future__ import annotations

from collections.abc import Sequence

from piolab.bench import Attach, NetSpec, Scenario, Wiring
from piolab.devices import (
    I2cMaster,
    I2cOp,
    I2cSlave,
    SpiMaster,
    SpiSlave,
    UartConfig,
    UartPartner,
    UartSend,
)


def uart_link(
    payload: Sequence[int] = (0x55, 0xA3, 0x00, 0xFF),
    baud: float = 1_000_000,
    parity: str = "none",
    stop_bits: float = 1,
    tx_ppm: float = 0.0,
    rx_ppm: float = 0.0,
    glitch_prob: float = 0.0,
    sends: Sequence[UartSend] | None = None,
    seed: int = 0,
    offset_s: float = 0.0,
    jitter_rms_s: float = 0.0,
) -> Scenario:
    cfg = UartConfig(baud=baud, parity=parity, stop_bits=stop_bits)
    tx = UartPartner("tx", UartConfig(baud=baud, parity=parity, stop_bits=stop_bits, ppm=tx_ppm,
                                      offset_s=offset_s, jitter_rms_s=jitter_rms_s),
                     send=list(sends) if sends is not None else list(payload),
                     glitch_prob=glitch_prob, glitch_cycles=2, seed=seed)
    rx = UartPartner("rx", UartConfig(baud=baud, parity=parity, stop_bits=stop_bits,
                                      ppm=rx_ppm) if rx_ppm else cfg)
    n = len(sends) if sends is not None else len(payload)
    bits = 1 + 8 + (parity != "none") + stop_bits
    cycles = int((n * (bits + 1) + 6) * 50e6 / baud)
    return Scenario("uart_link", Wiring({"line": NetSpec(pad="ui0")}),
                    [Attach(tx, {"tx": "line"}), Attach(rx, {"rx": "line"})],
                    max_cycles=cycles, requires_engine=False, seed=seed)


def spi_link(mode: int = 0, frames: Sequence[Sequence[int]] = ((0x96, 0x3C),),
             responses: Sequence[int] = (0xA5, 0x5A), msb_first: bool = True,
             sck_hz: float | None = None, offset_s: float = 0.0,
             jitter_rms_s: float = 0.0) -> Scenario:
    m = SpiMaster("m", frames, mode=mode, msb_first=msb_first, half_period=5, sck_hz=sck_hz,
                  offset_s=offset_s, jitter_rms_s=jitter_rms_s)
    s = SpiSlave("s", mode=mode, msb_first=msb_first, responses=responses)
    nets = {"sck": NetSpec(pad="ui1"), "mosi": NetSpec(pad="ui2"), "miso": NetSpec(pad="ui3"),
            "cs": NetSpec(pad="ui4")}
    words = sum(len(f) for f in frames)
    return Scenario("spi_link", Wiring(nets),
                    [Attach(m, {p: p for p in m.ports}), Attach(s, {p: p for p in s.ports})],
                    max_cycles=60 + words * 120 + len(frames) * 40, requires_engine=False)


def _i2c_wiring() -> Wiring:
    return Wiring({"scl": NetSpec(pad="ui5", pull=1), "sda": NetSpec(pad="ui6", pull=1)})


def i2c_link(stretch: int = 0, nack_after: int | None = None, nack_addr: bool = False,
             half: int = 25, scl_hz: float | None = None, jitter_rms_s: float = 0.0
             ) -> Scenario:
    m = I2cMaster("m", [I2cOp("write", 0x42, [0x10, 0x20, 0x30], stop=False),
                        I2cOp("read", 0x42, count=3)], half_period=half, scl_hz=scl_hz,
                  jitter_rms_s=jitter_rms_s)
    if scl_hz:
        half = round(50e6 / scl_hz / 2)
    s = I2cSlave("s", 0x42, read_data=[0xDE, 0xAD, 0xBE], stretch_cycles=stretch,
                 nack_after=nack_after, nack_addr=nack_addr)
    return Scenario("i2c_link", _i2c_wiring(),
                    [Attach(m, {"scl": "scl", "sda": "sda"}),
                     Attach(s, {"scl": "scl", "sda": "sda"})],
                    max_cycles=12 * 9 * 2 * half + 12 * stretch + 2000, requires_engine=False)


def i2c_arbitration(half: int = 25) -> Scenario:
    a = I2cMaster("a", [I2cOp("write", 0x42, [0x11])], half_period=half)
    b = I2cMaster("b", [I2cOp("write", 0x40, [0x22])], half_period=half)
    s = I2cSlave("s", 0x40)
    return Scenario("i2c_arbitration", _i2c_wiring(),
                    [Attach(x, {"scl": "scl", "sda": "sda"}) for x in (a, b, s)],
                    max_cycles=4 * 9 * 2 * half + 2000, requires_engine=False)

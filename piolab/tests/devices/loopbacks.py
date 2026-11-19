"""Device <-> device loopback scenarios (no engine needed).

Module-level factories so the cocotb backend can rebuild them by import path.
Nets are wired to DUT input pads (ui*) so that, under --backend=rtl, the
traffic also passes through the HDL testbench's ui_in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from piolab.bench import Attach, NetSpec, Scenario, Wiring
from piolab.devices import (
    CanFrame,
    CanNode,
    CanTiming,
    CanTransceiver,
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


# ---- CAN -------------------------------------------------------------------------------

CAN_FRAMES = (CanFrame(0x123, bytes([0xDE, 0xAD])), CanFrame(0x1ABCDE01, b"\x11", ext=True),
              CanFrame(0x7FF, rtr=True, dlc=4), CanFrame(0x000, bytes(8)))


def can_bus(nodes: Mapping[str, Mapping[str, Any]], bitrate: float = 1_000_000,
            sjw: int = 1, max_bits: int = 4000) -> Scenario:
    """CAN nodes on one open-drain net `can`. `nodes` maps a name to CanNode keyword
    arguments, plus `ppm` for that node's clock error. Stops when every node is idle
    with nothing left to send."""
    devs = []
    for name, kw in nodes.items():
        kw = dict(kw)
        ppm = kw.pop("ppm", 0.0)
        devs.append(CanNode(name, CanTiming(bitrate, sjw=sjw, ppm=ppm), **kw))
    names = list(nodes)
    return Scenario("can_bus", Wiring({"can": NetSpec(pad="ui7", pull=1)}),
                    [Attach(d, {"bus": "can"}) for d in devs],
                    max_cycles=int(max_bits * 50e6 / bitrate), requires_engine=False,
                    until=lambda s: all(s.devices[n].quiet for n in names))  # type: ignore[attr-defined]


def can_link(frames: Sequence[Any] = CAN_FRAMES, bitrate: float = 1_000_000,
             tx_ppm: float = 0.0, rx_ppm: float = 0.0, sjw: int = 1, ack: bool = True,
             retransmit: int | None = 8, max_bits: int = 4000) -> Scenario:
    """Node `a` sends `frames`; node `b` receives (and ACKs unless ack=False)."""
    return can_bus({"a": {"send": tuple(frames), "ppm": tx_ppm, "retransmit": retransmit},
                    "b": {"ppm": rx_ppm, "ack": ack}},
                   bitrate=bitrate, sjw=sjw, max_bits=max_bits)


def can_arbitration() -> Scenario:
    """Three nodes start in the same bit; the lowest identifier must win each round."""
    return can_bus({"a": {"send": (CanFrame(0x123, b"\xA1"),)},
                    "b": {"send": (CanFrame(0x122, b"\xB2"),)},
                    "c": {"send": (CanFrame(0x0FF, b"\xC3"),)}})


def can_transceiver_bus(nodes: Mapping[str, Mapping[str, Any]],
                        controller: Mapping[str, Any] | None = None,
                        tx_delay_s: float = 100e-9, rx_delay_s: float = 100e-9,
                        dominant_timeout_s: float | None = 1e-3, bitrate: float = 1_000_000,
                        sample_tq: int = 8, max_bits: int = 4000) -> Scenario:
    """Controller `ctl` (a CanNode with TXD/RXD pins) behind transceiver `xcvr`, plus
    `nodes` on the bus net `can`: the way a chip under test meets a CAN bus. Board-only
    nets `txd`, `rxd` join the controller and the transceiver."""
    timing = CanTiming(bitrate, sample_tq=sample_tq)
    ctl = CanNode("ctl", timing, pins="txd_rxd", **dict(controller or {}))
    xcvr = CanTransceiver("xcvr", tx_delay_s, rx_delay_s, dominant_timeout_s)
    devs = [Attach(ctl, {"tx": "txd", "rx": "rxd"}),
            Attach(xcvr, {"txd": "txd", "rxd": "rxd", "bus": "can"})]
    names = ["ctl"]
    for name, kw in nodes.items():
        devs.append(Attach(CanNode(name, CanTiming(bitrate), **kw), {"bus": "can"}))
        names.append(name)
    wiring = Wiring({"can": NetSpec(pad="ui7", pull=1), "txd": NetSpec(), "rxd": NetSpec()})
    return Scenario("can_transceiver_bus", wiring, devs,
                    max_cycles=int(max_bits * 50e6 / bitrate), requires_engine=False,
                    until=lambda s: all(s.devices[n].quiet for n in names))  # type: ignore[attr-defined]

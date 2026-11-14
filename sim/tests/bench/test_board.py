"""Board phase order, pad mapping and conflict handling (ADR-0002, ADR-0003)."""

import pytest

from sim.bench import Attach, BusConflict, NetSpec, PadIn, PadOut, Scenario, Wiring
from sim.bench.backends.python import run_python
from sim.devices import Device
from sim.dut import StubDut
from sim.wave import Level, X


class Toggler(Device):
    """Drives `o` = 1 from cycle `at` on; records what it sees on `i`."""

    ports = ("o", "i")

    def __init__(self, name: str, at: int = 3, value: int = 1) -> None:
        super().__init__(name)
        self.at, self.value = at, value
        self.seen: list[Level] = []

    def tick(self) -> None:
        self.seen.append(self.read("i"))
        if self.cycle >= self.at:
            self.drive("o", self.value)


class EchoDut(StubDut):
    """uo0 <= ui0 (registered); uio0 driven with OE from ui1; records inputs."""

    is_engine = False

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[PadIn] = []

    def step(self, pins_in: PadIn) -> PadOut:
        self.inputs.append(pins_in)
        oe = pins_in.ui >> 1 & 1
        return PadOut(uo=pins_in.ui & 1, uio_out=0, uio_oe=oe)


def _sc(devices: list[Attach], nets: dict[str, NetSpec], cycles: int = 10,
        on_conflict: str = "raise") -> Scenario:
    return Scenario("t", Wiring(nets), devices, cycles, requires_engine=False,
                    on_conflict=on_conflict)


def test_device_drive_is_seen_by_dut_same_cycle_and_by_devices_next_cycle() -> None:
    t = Toggler("t", at=3)
    dut = EchoDut()
    r = run_python(_sc([Attach(t, {"o": "in", "i": "out"})],
                       {"in": NetSpec(pad="ui0", pull=0), "out": NetSpec(pad="uo0")}), dut)
    # DUT samples the settled bus of cycle k
    assert [p.ui & 1 for p in dut.inputs[:6]] == [0, 0, 0, 1, 1, 1]
    # waveform records the settled bus
    assert r.waveform.at("in", 2) == 0 and r.waveform.at("in", 3) == 1
    # DUT output registered at the edge after cycle 3 appears at cycle 4;
    # the device sees it in its pre-step view at cycle 4.
    assert r.waveform.at("out", 3) == 0 and r.waveform.at("out", 4) == 1
    assert t.seen[:6] == [0, 0, 0, 0, 1, 1]


def test_unconnected_ui_reads_zero_and_floating_net_reads_x() -> None:
    dut = EchoDut()
    r = run_python(_sc([], {"float": NetSpec(pad="ui0")}, cycles=3), dut)
    assert r.waveform.at("float", 0) == X
    assert dut.inputs[0].ui_x == 1 and dut.inputs[0].ui == 0


def test_uio_oe_controls_drive_and_open_drain_pull_up() -> None:
    t = Toggler("t", at=2)  # sets ui1 -> DUT enables uio0 OE (drives 0)
    r = run_python(_sc([Attach(t, {"o": "en"})],
                       {"en": NetSpec(pad="ui1"), "sda": NetSpec(pad="uio0", pull=1)}),
                   EchoDut())
    # released -> pull-up; OE on from the edge after cycle 2 -> 0 at cycle 3
    assert [r.waveform.at("sda", c) for c in range(5)] == [1, 1, 1, 0, 0]


def test_push_pull_contention_raises() -> None:
    a, b = Toggler("a", at=1, value=1), Toggler("b", at=1, value=0)
    sc = _sc([Attach(a, {"o": "n"}), Attach(b, {"o": "n"})], {"n": NetSpec()})
    with pytest.raises(BusConflict, match="net 'n' at cycle 1"):
        run_python(sc, StubDut())


def test_contention_can_be_recorded() -> None:
    a, b = Toggler("a", at=1, value=1), Toggler("b", at=1, value=0)
    sc = _sc([Attach(a, {"o": "n"}), Attach(b, {"o": "n"})], {"n": NetSpec()}, cycles=3,
             on_conflict="record")
    r = run_python(sc, StubDut())
    assert len(r.conflicts) == 2 and r.waveform.at("n", 1) == X


def test_device_driving_against_dut_output_conflicts() -> None:
    t = Toggler("t", at=0, value=1)  # StubDut drives uo0 = 0
    with pytest.raises(BusConflict):
        run_python(_sc([Attach(t, {"o": "n"})], {"n": NetSpec(pad="uo0")}), StubDut())


def test_bad_port_and_net_are_rejected() -> None:
    t = Toggler("t")
    with pytest.raises(ValueError, match="no ports"):
        Attach(t, {"nope": "n"})
    with pytest.raises(ValueError, match="not in wiring"):
        _sc([Attach(t, {"o": "missing"})], {"n": NetSpec()})
    with pytest.raises(ValueError, match="more than one net"):
        Wiring({"a": NetSpec(pad="ui0"), "b": NetSpec(pad="ui0")})


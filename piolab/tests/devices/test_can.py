"""CAN node <-> CAN node on one wired-AND net, cross-checked by the independent decoder."""

import functools
from collections.abc import Sequence

from hypothesis import given, settings
from hypothesis import strategies as st

from piolab.bench.backends.python import run_python
from piolab.bench.scenario import Result
from piolab.checks import no_conflicts
from piolab.decoders import decode_can
from piolab.devices import CanFrame, CanSend
from piolab.dut.stub import StubDut

from .loopbacks import CAN_FRAMES, can_arbitration, can_bus, can_link

Row = tuple[int, bool, bool, int, bytes]


def _frames(r: Result, who: str, kind: str) -> list[Row]:
    return [(t.data["id"], t.data["ext"], t.data["rtr"], t.data["dlc"],
             bytes(t.data["data"]))
            for t in r.txns(who, kind)]


def _expected(frames: Sequence[CanFrame]) -> list[Row]:
    out: list[Row] = []
    for f in frames:
        dlc = len(f.data) if f.dlc is None else f.dlc
        out.append((f.id, f.ext, f.rtr, dlc, b"" if f.rtr else f.data))
    return out


def _errors(r: Result, who: str) -> list[str]:
    return [t.data["type"] for t in r.txns(who, "error")]


def test_link_delivers_frames(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(can_link)
    no_conflicts(r)
    assert _frames(r, "b", "rx") == _expected(CAN_FRAMES)
    assert _frames(r, "a", "sent") == _expected(CAN_FRAMES)
    assert [t.data["attempt"] for t in r.txns("a", "sent")] == [1, 1, 1, 1]
    assert len(r.txns("b", "ack")) == 4
    assert _errors(r, "a") == [] and _errors(r, "b") == []
    dec = decode_can(r.waveform, "can", 1_000_000)
    assert [(f.id, f.ext, f.rtr, f.dlc, f.data) for f in dec] == _expected(CAN_FRAMES)
    assert all(f.crc_ok and f.acked and f.error is None for f in dec)


def test_arbitration_lowest_id_wins(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(can_arbitration)
    no_conflicts(r)
    assert [f.id for f in decode_can(r.waveform, "can", 1_000_000)] == [0x0FF, 0x122, 0x123]
    # 0x0FF = 000 1111 1111 beats both at ID bit 2 (frame bit 3); 0x122 beats 0x123 at bit 11
    assert [t.data["bit"] for t in r.txns("a", "arb_lost")] == [3, 11]
    assert [t.data["bit"] for t in r.txns("b", "arb_lost")] == [3]
    assert r.txns("c", "arb_lost") == []
    assert [t.data["id"] for t in r.txns("c", "rx")] == [0x122, 0x123]
    assert all(_errors(r, n) == [] for n in "abc")


def test_standard_frame_beats_extended_with_same_base_id(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(can_bus, nodes={
        "a": {"send": (CanFrame(0x123 << 18 | 0x5, b"\x01", ext=True),)},
        "b": {"send": (CanFrame(0x123, b"\x02"),)}}))
    assert [t.data["bit"] for t in r.txns("a", "arb_lost")] == [12]  # SRR (1) vs RTR (0)
    assert [f.ext for f in decode_can(r.waveform, "can", 1_000_000)] == [False, True]


def test_crc_error_is_flagged_and_retransmitted(bench) -> None:  # type: ignore[no-untyped-def]
    f = CanFrame(0x55, b"\x01\x02")
    r = bench(functools.partial(can_link, frames=(CanSend(f, crc_error=True),)))
    no_conflicts(r)
    assert _errors(r, "b") == ["crc"]
    assert "ack" in _errors(r, "a")  # b withholds its ACK
    assert len(r.txns("b", "error_flag")) == 1
    assert [t.data["attempt"] for t in r.txns("a", "sent")] == [2]
    assert _frames(r, "b", "rx") == _expected([f])
    dec = decode_can(r.waveform, "can", 1_000_000)
    assert [d.error for d in dec] == ["crc", None]


@functools.cache
def _fault_cases() -> dict[str, tuple[CanSend, str]]:
    z = CanFrame(0x000, bytes(2))
    return {"stuff": (CanSend(z, stuff_error_at=0), "stuff"),
            "crc_delim": (CanSend(z, form_error="crc_delim"), "form"),
            "ack_delim": (CanSend(z, form_error="ack_delim"), "form"),
            "eof": (CanSend(z, form_error="eof"), "form")}


def test_injected_faults_are_detected(bench) -> None:  # type: ignore[no-untyped-def]
    for name, (send, kind) in _fault_cases().items():
        r = bench(functools.partial(can_link, frames=(send,)))
        assert _errors(r, "b")[:1] == [kind], name
        assert [t.data["attempt"] for t in r.txns("a", "sent")] == [2], name
        assert len(r.txns("b", "rx")) == 1, name


def test_flipped_data_bit_is_caught(bench) -> None:  # type: ignore[no-untyped-def]
    f = CanFrame(0x2AA, bytes([0x5A]))
    r = bench(functools.partial(can_link, frames=(CanSend(f, flip_bit=22),)))  # inside data
    assert _errors(r, "b")[:1] in (["crc"], ["stuff"])
    assert [t.data["attempt"] for t in r.txns("a", "sent")] == [2]


def test_missing_ack_makes_the_sender_error_passive(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(can_link, frames=(CanFrame(0x10, b"\x01"),), ack=False,
                                retransmit=20, max_bits=8000))
    states = [t.data["state"] for t in r.txns("a", "state")]
    assert states == ["error_passive"]
    (abort,) = r.txns("a", "tx_abort")
    assert abort.data["attempts"] == 21
    assert abort.data["tec"] == 128  # a passive transmitter's ACK error does not count
    assert r.txns("a", "sent") == []


def test_bus_off_and_recovery(bench) -> None:  # type: ignore[no-untyped-def]
    f = CanFrame(0x10, b"\x01")
    r = bench(functools.partial(can_link, frames=(CanSend(f, crc_error=True, fault_attempts=32),),
                                retransmit=None, max_bits=12000))
    assert [t.data["state"] for t in r.txns("a", "state")] == \
        ["error_passive", "bus_off", "error_active"]
    assert [t.data["attempt"] for t in r.txns("a", "sent")] == [33]
    assert _frames(r, "b", "rx")[-1:] == _expected([f])


def test_glitch_on_idle_bus_is_ignored(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(can_bus, nodes={
        "a": {"send": (CanFrame(0x321, b"\x07"),), "start_delay_bits": 30},
        "b": {"glitches": ((1000, 3),)}}))
    assert _errors(r, "a") == [] and _errors(r, "b") == []
    assert [t.data["id"] for t in r.txns("b", "rx")] == [0x321]


@settings(max_examples=10, deadline=None, derandomize=True)
@given(tx_ppm=st.integers(-4000, 4000), rx_ppm=st.integers(-4000, 4000),
       ident=st.integers(0, 0x7FF), data=st.binary(min_size=0, max_size=8))
def test_clock_tolerance(tx_ppm: int, rx_ppm: int, ident: int, data: bytes) -> None:
    """+/-0.4 % per node is inside the CAN oscillator tolerance for 10 tq, sample at 80 %,
    SJW 2 (min(SJW/(20*NBT), min(ph1, ph2)/(2*(13*NBT - ph2))) = 0.78 %)."""
    f = CanFrame(ident, data)
    r = run_python(can_link(frames=(f, f), tx_ppm=tx_ppm, rx_ppm=rx_ppm, sjw=2), StubDut())
    assert _frames(r, "b", "rx") == _expected([f, f])
    assert _errors(r, "a") == [] and _errors(r, "b") == []
    dec = decode_can(r.waveform, "can", 1_000_000)
    assert [(d.id, d.data, d.crc_ok) for d in dec] == [(ident, data, True)] * 2


def test_large_clock_error_breaks_the_link() -> None:
    """Sanity: the clock-error injection is real (3 % is far outside the tolerance)."""
    f = CanFrame(0x000, bytes(8))
    r = run_python(can_link(frames=(f,), tx_ppm=30_000, retransmit=0), StubDut())
    assert _errors(r, "b") != [] or _frames(r, "b", "rx") != _expected([f])


def test_overwritten_stuff_bit_in_arbitration_is_a_bit_error(bench) -> None:  # type: ignore[no-untyped-def]
    """Stuff bits are not part of arbitration (ISO 11898-1): a recessive stuff bit read
    back dominant is a bit error, not lost arbitration. 0x07F = SOF + 0000 -> wire bit 5
    is a recessive stuff bit; `a` starts at cycle 550, so it is sampled near cycle 840."""
    r = bench(functools.partial(can_bus, nodes={
        "a": {"send": (CanFrame(0x07F, b"\x01"),)},
        "b": {"glitches": ((830, 25),)}}))
    assert r.txns("a", "arb_lost") == []
    assert [(t.data["type"], t.data["role"]) for t in r.txns("a", "error")] == [("bit", "tx")]
    assert [t.data["attempt"] for t in r.txns("a", "sent")] == [2]


# ---- a controller behind a transceiver ----------------------------------------------------

from .loopbacks import can_transceiver_bus  # noqa: E402


def test_controller_behind_transceiver_sends_and_receives(bench) -> None:  # type: ignore[no-untyped-def]
    """TXD/RXD through a transceiver with a 200 ns loop delay: frames both ways, ACKed."""
    out, back = CanFrame(0x123, b"\xDE\xAD"), CanFrame(0x321, b"\x42")
    r = bench(functools.partial(can_transceiver_bus, controller={"send": (out,)},
                                nodes={"b": {"send": (back,), "start_delay_bits": 200}}))
    no_conflicts(r)
    assert _frames(r, "b", "rx") == _expected([out])
    assert _frames(r, "ctl", "rx") == _expected([back])
    assert _errors(r, "ctl") == [] and _errors(r, "b") == []
    assert [(f.id, f.acked) for f in decode_can(r.waveform, "can", 1_000_000)] == \
        [(0x123, True), (0x321, True)]
    # RXD is the bus, delayed: the controller's own frame comes back on it
    rx_frames = decode_can(r.waveform, "rxd", 1_000_000)
    assert [f.id for f in rx_frames][:1] == [0x123]


def test_arbitration_through_the_transceiver(bench) -> None:  # type: ignore[no-untyped-def]
    """The loop delay is inside the sample point, so a controller behind a transceiver
    still loses arbitration cleanly to a lower identifier and resends afterwards."""
    r = bench(functools.partial(can_transceiver_bus,
                                controller={"send": (CanFrame(0x123, b"\x01"),)},
                                nodes={"b": {"send": (CanFrame(0x122, b"\x02"),)}}))
    assert [t.data["id"] for t in r.txns("ctl", "arb_lost")] == [0x123]
    assert [f.id for f in decode_can(r.waveform, "can", 1_000_000)] == [0x122, 0x123]
    assert _errors(r, "ctl") == [] and _errors(r, "b") == []


def test_loop_delay_past_the_sample_point_breaks_transmission(bench) -> None:  # type: ignore[no-untyped-def]
    """The controller reads its own bits back late: at 1 Mbit/s with the sample point at
    80 %, a loop delay of 0.9 us lands the read-back in the next bit, a bit error."""
    r = bench(functools.partial(can_transceiver_bus,
                                controller={"send": (CanFrame(0x0F0, b"\x0F"),), "retransmit": 0},
                                nodes={"b": {}}, tx_delay_s=450e-9, rx_delay_s=450e-9))
    assert "bit" in _errors(r, "ctl")
    assert r.txns("ctl", "sent") == []


def test_dominant_timeout_releases_the_bus(bench) -> None:  # type: ignore[no-untyped-def]
    """A controller stuck dominant (here a 2000-cycle glitch on TXD) can't hold the bus:
    the transceiver releases it after the time-out, and the other nodes recover."""
    r = bench(functools.partial(can_transceiver_bus, controller={"glitches": ((1000, 2000),)},
                                nodes={"b": {"send": (CanFrame(0x55, b"\x01"),),
                                             "start_delay_bits": 80}},
                                dominant_timeout_s=10e-6))
    assert len(r.txns("xcvr", "dominant_timeout")) == 1
    bus = r.waveform
    # 10 us = 500 cycles of dominant TXD, plus the TXD -> bus delay: released by ~1610
    assert bus.at("can", 1800) == 1 and bus.at("txd", 1800) == 0
    assert [t.data["id"] for t in r.txns("ctl", "rx")] == [0x55]

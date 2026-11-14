import functools

from pioscope.bench import Result
from pioscope.checks import CheckFailed, i2c_timing, no_conflicts
from pioscope.decoders import decode_i2c

from .loopbacks import i2c_arbitration, i2c_link


def _kinds(r, who):  # type: ignore[no-untyped-def]
    return [(t.kind, t.data) for t in r.txns(who) if t.kind != "stretch"]


def test_write_repeated_start_read(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(i2c_link)
    no_conflicts(r)
    assert [t.data["byte"] for t in r.txns("s", "write")] == [0x10, 0x20, 0x30]
    assert [t.data["byte"] for t in r.txns("m", "read")] == [0xDE, 0xAD, 0xBE]
    assert [t.data["master_ack"] for t in r.txns("s", "read")] == [True, True, False]
    ev = decode_i2c(r.waveform, "scl", "sda")
    assert [(e.kind, e.value, e.ack) for e in ev] == [
        ("start", None, None), ("addr", 0x42, True), ("data", 0x10, True), ("data", 0x20, True),
        ("data", 0x30, True), ("restart", None, None), ("addr", 0x42, True),
        ("data", 0xDE, True), ("data", 0xAD, True), ("data", 0xBE, False), ("stop", None, None)]


def test_clock_stretching_slows_the_bus(bench) -> None:  # type: ignore[no-untyped-def]
    plain = bench(i2c_link)
    stretched = bench(functools.partial(i2c_link, stretch=300))
    assert [t.data for t in stretched.txns("s", "write")] == \
        [t.data for t in plain.txns("s", "write")]
    def end(r: Result) -> int:
        return r.txns("m", "done")[0].cycle

    assert end(stretched) - end(plain) >= 300 * 4
    lows = [t for e in decode_i2c(stretched.waveform, "scl", "sda") for t in e.t_low]
    assert max(lows) >= 300


def test_nack_on_data_and_address(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(i2c_link, nack_after=1))
    assert [t.data["ack"] for t in r.txns("m", "write")] == [True, False]
    r = bench(functools.partial(i2c_link, nack_addr=True))
    assert [t.data["ack"] for t in r.txns("m", "addr")] == [False, False]
    assert r.txns("s", "write") == []


def test_multi_master_arbitration(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(i2c_arbitration)
    no_conflicts(r)
    lost = r.txns("a", "arb_lost")
    # 0x42 = 1000010 vs 0x40 = 1000000: 'a' sends the first 1 where 'b' sends 0 (bit 5)
    assert [t.data for t in lost] == [{"phase": "addr", "bit": 5}]
    assert [t.data["byte"] for t in r.txns("s", "write")] == [0x22]
    ev = decode_i2c(r.waveform, "scl", "sda")
    assert [(e.kind, e.value) for e in ev if e.kind in ("addr", "data")] == [("addr", 0x40),
                                                                             ("data", 0x22)]


def test_timing_check_catches_fast_clock(bench) -> None:  # type: ignore[no-untyped-def]
    from pioscope.checks import I2C_STANDARD

    r = bench(i2c_link)  # half period 25 cycles = 0.5 us: far too fast for 100 kHz mode
    ev = decode_i2c(r.waveform, "scl", "sda")
    try:
        i2c_timing(r, ev, I2C_STANDARD)
    except CheckFailed as e:
        assert "tLOW" in str(e) or "tHIGH" in str(e)
    else:
        raise AssertionError("expected a timing violation")
    slow = bench(functools.partial(i2c_link, half=250))  # 100 kHz at 50 MHz
    i2c_timing(slow, decode_i2c(slow.waveform, "scl", "sda"), I2C_STANDARD)

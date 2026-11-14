import functools

from hypothesis import given, settings
from hypothesis import strategies as st

from sim.bench import Result
from sim.bench.backends.python import run_python
from sim.checks import expect_sequence, no_conflicts, uart_bit_period
from sim.decoders import decode_uart
from sim.devices import UartSend
from sim.dut import StubDut

from .loopbacks import uart_link


def _rx(r: Result) -> list[int]:
    return [t.data["value"] for t in r.txns("rx", "rx")]


def test_8n1_both_ways(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(uart_link)
    no_conflicts(r)
    expect_sequence("partner rx", [0x55, 0xA3, 0x00, 0xFF], _rx(r))
    frames = decode_uart(r.waveform, "line", baud=1_000_000)
    expect_sequence("decoder", [0x55, 0xA3, 0x00, 0xFF], [f.value for f in frames])
    uart_bit_period(r, frames, 1_000_000, tol_ppm=1000)


def test_parity_and_stop_bits(bench) -> None:  # type: ignore[no-untyped-def]
    for parity in ("even", "odd"):
        for stop in (1, 1.5, 2):
            r = bench(functools.partial(uart_link, parity=parity, stop_bits=stop))
            assert all(t.data["parity_ok"] and t.data["framing_ok"] for t in r.txns("rx", "rx"))
            assert _rx(r) == [0x55, 0xA3, 0x00, 0xFF]
            frames = decode_uart(r.waveform, "line", 1_000_000, parity=parity, stop_bits=stop)
            assert [f.value for f in frames] == [0x55, 0xA3, 0x00, 0xFF]


def test_injected_errors_are_detected(bench) -> None:  # type: ignore[no-untyped-def]
    sends = [UartSend(0x12), UartSend(0x34, parity_error=True), UartSend(0x56, framing_error=True),
             UartSend(break_bits=14, gap_bits=1), UartSend(0x78, gap_bits=2)]
    r = bench(functools.partial(uart_link, parity="even", sends=sends))
    rx = r.txns("rx", "rx")
    assert [(t.data["value"], t.data["parity_ok"], t.data["framing_ok"]) for t in rx] == [
        (0x12, True, True), (0x34, False, True), (0x56, True, False), (0x78, True, True)]
    assert len(r.txns("rx", "break")) == 1
    frames = decode_uart(r.waveform, "line", 1_000_000, parity="even")
    assert [f.is_break for f in frames] == [False, False, False, True, False]
    assert [f.parity_ok for f in frames if not f.is_break] == [True, False, True, True]


@settings(max_examples=25, deadline=None, derandomize=True)
@given(payload=st.lists(st.integers(0, 255), min_size=1, max_size=6),
       ppm=st.integers(-20_000, 20_000))
def test_round_trip_under_baud_error(payload: list[int], ppm: int) -> None:
    """+/-2% clock mismatch is within what an 8N1 receiver sampling mid-bit tolerates."""
    r = run_python(uart_link(payload=payload, tx_ppm=ppm), StubDut())
    assert _rx(r) == payload
    frames = decode_uart(r.waveform, "line", baud=1_000_000 * (1 + ppm * 1e-6))
    assert [f.value for f in frames] == payload


def test_large_baud_error_breaks_the_link() -> None:
    """Sanity: the fault injection is real (6% error corrupts frames)."""
    r = run_python(uart_link(payload=[0x00] * 4, tx_ppm=60_000), StubDut())
    assert _rx(r) != [0x00] * 4 or any(not t.data["framing_ok"] for t in r.txns("rx", "rx"))


def test_glitches_are_seeded_and_reproducible() -> None:
    a = run_python(uart_link(glitch_prob=0.3, seed=7), StubDut())
    b = run_python(uart_link(glitch_prob=0.3, seed=7), StubDut())
    c = run_python(uart_link(glitch_prob=0.3, seed=8), StubDut())
    assert a.waveform.digest() == b.waveform.digest() != c.waveform.digest()

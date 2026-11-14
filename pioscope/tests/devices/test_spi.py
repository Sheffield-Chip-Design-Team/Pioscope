import functools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pioscope.bench.backends.python import run_python
from pioscope.checks import spi_timing
from pioscope.decoders import decode_spi
from pioscope.dut import StubDut

from .loopbacks import spi_link


@pytest.mark.parametrize("mode", range(4))
def test_full_duplex_all_modes(bench, mode: int) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(spi_link, mode=mode))
    pairs = [(t.data["mosi"], t.data["miso"]) for t in r.txns("m", "word")]
    assert pairs == [(0x96, 0xA5), (0x3C, 0x5A)]
    assert [(t.data["mosi"], t.data["miso"]) for t in r.txns("s", "word")] == pairs
    words = decode_spi(r.waveform, "sck", "mosi", "miso", "cs", mode=mode)
    assert [(w.mosi, w.miso) for w in words] == pairs
    spi_timing(r, words, min_setup_cycles=1, min_hold_cycles=1)


def test_lsb_first_and_cs_frames(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(spi_link, frames=((0x01,), (0x80, 0x02)), responses=(1, 2, 3),
                                msb_first=False))
    assert [t.data["mosi"] for t in r.txns("s", "word")] == [0x01, 0x80, 0x02]
    assert [t.data["miso"] for t in r.txns("m", "word")] == [1, 2, 3]
    assert len(r.txns("s", "select")) == 2
    words = decode_spi(r.waveform, "sck", "mosi", "miso", "cs", mode=0, msb_first=False)
    assert [w.mosi for w in words] == [0x01, 0x80, 0x02]


@settings(max_examples=20, deadline=None, derandomize=True)
@given(mode=st.integers(0, 3), words=st.lists(st.integers(0, 255), min_size=1, max_size=4))
def test_random_words(mode: int, words: list[int]) -> None:
    resp = [w ^ 0xFF for w in words]
    r = run_python(spi_link(mode=mode, frames=(tuple(words),), responses=resp), StubDut())
    assert [t.data["mosi"] for t in r.txns("s", "word")] == words
    assert [t.data["miso"] for t in r.txns("m", "word")] == resp

"""Asynchronous device clocks and the DUT sampling window (ADR-0006)."""

import functools
import itertools

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from piolab.bench import Attach, NetSpec, PadIn, PadOut, Scenario, Wiring
from piolab.bench.backends.python import run_python
from piolab.bench.parity import parity_async_scenario, parity_scenario
from piolab.decoders import decode_i2c, decode_spi, decode_uart
from piolab.devices import At, ScriptedDevice
from piolab.dut import StubDut, TtTemplateDut
from piolab.tests.devices.loopbacks import i2c_link, spi_link, uart_link

FS = 50e6
T = 1 / FS


class EdgeAt(ScriptedDevice):
    """Drives `o` high at time t (seconds)."""

    ports = ("o",)

    def __init__(self, t: float) -> None:
        super().__init__("e")
        self.t = t
        self.drive("o", 0)

    def script(self):  # type: ignore[no-untyped-def]
        yield At(self.t)
        self.drive("o", 1)


class SeenDut(StubDut):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[int] = []

    def step(self, pins_in: PadIn) -> PadOut:
        self.seen.append(pins_in.ui & 1)
        return PadOut()


def _run(t: float, aperture: float, seed: int = 0) -> tuple[SeenDut, object]:
    dut = SeenDut()
    sc = Scenario("edge", Wiring({"n": NetSpec(pad="ui0", pull=0)}),
                  [Attach(EdgeAt(t), {"o": "n"})], 20, requires_engine=False,
                  sample_aperture_s=aperture, seed=seed)
    return dut, run_python(sc, dut)


# Digests recorded before ADR-0006: zero aperture + untimed drives are bit-identical.
BASELINE = {
    "spi_mode3": "7c2e79e2500e4c4c7e0994eda6bc77eda7fa1b77532204685fde5285cf60560c",
    "i2c_stretch40": "608ab18361caf82ed5f07165c8c5b2c23f23e222d92f7087f7f6c7dfd642fe70",
}


def test_synchronous_runs_are_bit_identical_to_before() -> None:
    assert run_python(spi_link(mode=3), StubDut()).waveform.digest() == BASELINE["spi_mode3"]
    assert run_python(i2c_link(stretch=40), StubDut()).waveform.digest() == \
        BASELINE["i2c_stretch40"]


def test_timed_edge_is_recorded_with_its_sub_cycle_position() -> None:
    _, r = _run(5.3 * T, 0.0)
    assert r.waveform.at("n", 4) == 0 and r.waveform.at("n", 5) == 1  # type: ignore[attr-defined]
    assert r.waveform.time_of("n", 5) == pytest.approx(5.3)  # type: ignore[attr-defined]


def test_edge_far_from_sampling_edge_is_never_metastable() -> None:
    for seed in range(20):
        dut, r = _run(5.5 * T, 0.2 * T, seed)
        assert dut.seen[4:7] == [0, 1, 1]
        assert r.stats["metastable_windows"] == 0  # type: ignore[attr-defined]


def test_edge_inside_window_is_seen_this_cycle_or_next_never_otherwise() -> None:
    outcomes = set()
    for seed in range(40):
        dut, r = _run(5.95 * T, 0.1 * T, seed)
        assert r.stats["metastable_windows"] == 1  # type: ignore[attr-defined]
        assert dut.seen[4] == 0 and dut.seen[6:] == [1] * len(dut.seen[6:])
        outcomes.add(dut.seen[5])
        # dut:<net> shows exactly what was sampled
        assert r.waveform.at("dut:n", 5) == dut.seen[5]  # type: ignore[attr-defined]
    assert outcomes == {0, 1}


def test_metastable_resolution_is_reproducible_per_seed() -> None:
    a = [_run(5.95 * T, 0.1 * T, s)[0].seen for s in range(10)]
    b = [_run(5.95 * T, 0.1 * T, s)[0].seen for s in range(10)]
    assert a == b


def test_two_changes_in_one_cycle_are_counted_as_a_lost_pulse() -> None:
    class Pulse(ScriptedDevice):
        ports = ("o",)

        def script(self):  # type: ignore[no-untyped-def]
            self.drive("o", 0)
            yield At(3.2 * T)
            self.drive("o", 1)
            yield At(3.6 * T)
            self.drive("o", 0)

    sc = Scenario("p", Wiring({"n": NetSpec()}), [Attach(Pulse("p"), {"o": "n"})], 10,
                  requires_engine=False)
    r = run_python(sc, StubDut())
    assert r.stats["subcycle_pulses_lost"] == 1
    assert r.waveform.changes["n"] == [(0, 0)]  # the pulse never reaches the bus grid


def test_async_parity_scenario_exercises_the_window() -> None:
    r = run_python(parity_async_scenario(), TtTemplateDut())
    assert r.stats["metastable_windows"] > 0 and r.stats["late_samples"] > 0
    assert r.stats["subcycle_pulses_lost"] == 0
    for net in ("u_tx", "sck", "mosi"):
        sampled, real = r.waveform.edges(f"dut:{net}"), r.waveform.edges(net)
        assert len(sampled) == len(real)
        assert all(0 <= s[0] - e[0] <= 1 and s[1] == e[1]
                   for s, e in zip(sampled, real, strict=True))


def test_vcd_places_async_edges_between_sysclk_ticks(tmp_path) -> None:  # type: ignore[no-untyped-def]
    r = run_python(parity_async_scenario(), TtTemplateDut())
    r.waveform.to_vcd(tmp_path / "a.vcd")
    stamps = [int(line[1:]) for line in (tmp_path / "a.vcd").read_text().splitlines()
              if line.startswith("#")]
    assert any(s % 20_000 for s in stamps)
    assert "dut_u_tx" in (tmp_path / "a.vcd").read_text()


def test_sync_parity_unchanged_by_dut_view() -> None:
    assert "dut:u_tx" not in run_python(parity_scenario(), TtTemplateDut()).waveform.nets


# ---- devices on their own clocks ---------------------------------------------------------


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("offset", [0.0, 3e-9, 11e-9, 19e-9])
def test_spi_at_non_integer_sck(bench, mode: int, offset: float) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(spi_link, mode=mode, sck_hz=12.34e6, offset_s=offset,
                                jitter_rms_s=0.3e-9))
    pairs = [(t.data["mosi"], t.data["miso"]) for t in r.txns("m", "word")]
    assert pairs == [(0x96, 0xA5), (0x3C, 0x5A)]
    words = decode_spi(r.waveform, "sck", "mosi", "miso", "cs", mode=mode)
    assert [(w.mosi, w.miso) for w in words] == pairs
    assert r.stats["subcycle_pulses_lost"] == 0


def test_i2c_at_non_integer_scl(bench) -> None:  # type: ignore[no-untyped-def]
    r = bench(functools.partial(i2c_link, scl_hz=97.3e3, jitter_rms_s=20e-9))
    assert [t.data["byte"] for t in r.txns("s", "write")] == [0x10, 0x20, 0x30]
    assert [t.data["byte"] for t in r.txns("m", "read")] == [0xDE, 0xAD, 0xBE]
    ev = decode_i2c(r.waveform, "scl", "sda")
    assert [e.value for e in ev if e.kind == "data"] == [0x10, 0x20, 0x30, 0xDE, 0xAD, 0xBE]


@settings(max_examples=30, deadline=None, derandomize=True)
@given(phase=st.integers(0, 7), ppm=st.integers(-20_000, 20_000),
       payload=st.lists(st.integers(0, 255), min_size=1, max_size=4))
def test_uart_phase_and_baud_sweep(phase: int, ppm: int, payload: list[int]) -> None:
    r = run_python(uart_link(payload=payload, tx_ppm=ppm, offset_s=phase * T / 8,
                             jitter_rms_s=0.5e-9), StubDut())
    assert [t.data["value"] for t in r.txns("rx", "rx")] == payload
    assert [f.value for f in decode_uart(r.waveform, "line", 1e6 * (1 + ppm * 1e-6))] == payload


def test_device_clocks_are_drawn_in_the_vcd(tmp_path) -> None:  # type: ignore[no-untyped-def]
    r = run_python(parity_async_scenario(), TtTemplateDut())
    clk = r.waveform.clocks
    assert set(clk) == {"clk:uart.bit_clk", "clk:mon.bit_clk", "clk:spi.sck_clk"}
    # UART: one pulse per bit boundary, spaced by the partner's (ppm-shifted) bit time
    rises = [t for t, v in clk["clk:uart.bit_clk"] if v == 1]
    period = 1 / (2_345_678 * (1 + 1500e-6))
    gaps = [b - a for a, b in itertools.pairwise(rises)]
    assert all(abs(g - period) < 6 * 0.4e-9 for g in gaps)
    # SPI: free-running SCK at sck_hz
    toggles = [t for t, _ in clk["clk:spi.sck_clk"][1:]]
    assert abs((toggles[-1] - toggles[0]) / (len(toggles) - 1) - 1 / (2 * 12.34e6)) < 1e-11
    r.waveform.to_vcd(tmp_path / "c.vcd")
    text = (tmp_path / "c.vcd").read_text()
    assert "$scope module clocks $end" in text and "uart_bit_clk" in text
    # clocks are display-only: decoders and cycle grid untouched
    assert "clk:uart.bit_clk" not in r.waveform.changes


def test_device_clocks_can_be_turned_off() -> None:
    sc = parity_async_scenario()
    sc.record_device_clocks = False
    assert run_python(sc, TtTemplateDut()).waveform.clocks == {}

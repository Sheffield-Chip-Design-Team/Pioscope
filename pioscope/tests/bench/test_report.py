from pioscope.bench import HostScript, Scenario, Wiring
from pioscope.bench.backends.python import run_python
from pioscope.bench.parity import parity_async_scenario
from pioscope.bench.report import render_log, timeline
from pioscope.devices import Txn
from pioscope.dut import StubDut, TtTemplateDut
from pioscope.tests.devices.loopbacks import uart_link


def test_events_carry_exact_time_and_are_merged_in_order() -> None:
    r = run_python(parity_async_scenario(), TtTemplateDut())
    evs = timeline(r)
    assert [e.t for e in evs] == sorted(e.t for e in evs)
    assert {e.source for e in evs} == {"uart", "mon", "spi"}
    fs = r.waveform.sysclk_hz
    for e in evs:  # every timestamp lies inside its DUT cycle
        assert e.cycle / fs <= e.t < (e.cycle + 1) / fs + 1e-15
    assert any(e.t * fs % 1 > 1e-6 for e in evs)  # some are sub-cycle (device clocks)


def test_render_log_format_limit_and_filter() -> None:
    r = run_python(uart_link(), StubDut())
    text = render_log(r, limit=3, sources=["rx"])
    lines = text.splitlines()
    assert lines[0].split() == ["time", "cycle", "source", "event", "data"]
    assert len(lines) == 1 + 3 + 1 and lines[-1].startswith("... 1 more")
    assert " us  @" in lines[1] and " rx " in lines[1] and "value=0x55" in lines[1]


def test_live_events_match_the_final_log() -> None:
    seen: list[tuple[str, Txn]] = []
    r = run_python(uart_link(), StubDut(), on_event=lambda s, t: seen.append((s, t)))
    assert sorted((s, t.cycle, t.kind) for s, t in seen) == sorted(
        (s, t.cycle, t.kind) for s, ts in r.logs.items() for t in ts)


def test_host_pushes_are_logged_with_time() -> None:
    sc = Scenario("h", Wiring({}), [], 10, host=HostScript(tx={0: [7, 8]}),
                  requires_engine=False)
    r = run_python(sc, StubDut())
    assert [(t.cycle, t.kind, t.data) for t in r.logs["host"]] == [
        (0, "push", {"core": 0, "word": 7}), (1, "push", {"core": 0, "word": 8})]
    assert r.logs["host"][1].t == 1 / 50e6

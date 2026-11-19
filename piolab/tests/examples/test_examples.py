"""The tutorials' examples run as described (docs/tutorials/)."""

from __future__ import annotations

from pathlib import Path

from piolab.asm import assemble_file
from piolab.bench.backends.python import run_python
from piolab.debug.repl import Repl
from piolab.debug.session import DebugSession
from piolab.examples import EXAMPLES_DIR
from piolab.examples.edge_counter import EdgeCountingEngine
from piolab.examples.pulse_counter import blink_scenario, decode_pulses
from piolab.packs.pio.models.base import PioEngine


def test_01_blink_assembles_to_two_words() -> None:
    image = assemble_file(EXAMPLES_DIR / "blink.pio")
    assert image.size == 2 and image.encodable


def test_02_device_and_decoder_agree() -> None:
    r = run_python(blink_scenario(5), PioEngine())
    pulses = decode_pulses(r.waveform, "led")
    assert len(pulses) >= 5
    assert all(p.width == 200 for p in pulses)  # 8 SM cycles x clkdiv 25
    assert all(p.period == 400 for p in pulses if p.period is not None)
    logged = [t.data["width"] for t in r.txns("counter", "pulse")]
    assert logged[:5] == [200] * 5


def test_03_04_unit_shows_up_everywhere(tmp_path: Path) -> None:
    r = run_python(blink_scenario(3), EdgeCountingEngine())
    edges = r.waveform.probes["sm0.units.edge_counter.edges"]
    assert edges[-1][1] >= 6
    s = DebugSession(blink_scenario, dut=EdgeCountingEngine)
    s.stepi(1000)
    assert s.eval("units.edge_counter.edges") == 5  # first set at cycle 1, then every 200
    import io

    buf = io.StringIO()
    Repl(s, stdout=buf).onecmd("info units edge_counter")
    assert "edge_counter: edges=5" in buf.getvalue()

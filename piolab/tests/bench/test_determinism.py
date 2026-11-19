from piolab.bench.backends.python import run_python
from piolab.bench.parity import parity_scenario
from piolab.dut import TtTemplateDut


def test_same_scenario_same_waveform() -> None:
    a = run_python(parity_scenario(), TtTemplateDut())
    b = run_python(parity_scenario(), TtTemplateDut())
    assert a.waveform.digest() == b.waveform.digest()
    assert a.to_dict() == b.to_dict()


def test_result_round_trips_through_json(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from piolab.bench import Result

    r = run_python(parity_scenario(cycles=500), TtTemplateDut())
    r.save(tmp_path / "r.json")
    assert Result.load(tmp_path / "r.json").to_dict() == r.to_dict()


def test_vcd_export(tmp_path) -> None:  # type: ignore[no-untyped-def]
    r = run_python(parity_scenario(cycles=500), TtTemplateDut())
    r.waveform.to_vcd(tmp_path / "w.vcd")
    text = (tmp_path / "w.vcd").read_text()
    assert "$var wire 1" in text and "u_tx" in text

"""The `piolab` command line: program names, `--isa` anywhere, device specs, models."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from piolab import config
from piolab.cli import _pop_isa, main
from piolab.devices.spec import DeviceSpecError, parse
from piolab.packs import get
from piolab.run import RunError, RunOptions, resolve_program, resolve_scenario

PIO = get("pio")


@pytest.fixture(autouse=True)
def _pio() -> Iterator[None]:
    """The PIO pack is active; `main(["--isa", ...])` calls must not leak past a test."""
    import piolab.packs as packs

    saved = list(packs._override)
    with packs.use("pio"):
        yield
    packs._override[:] = saved


def test_isa_is_taken_from_anywhere_on_the_line() -> None:
    assert _pop_isa(["sim", "x", "--isa", "pio", "-m", "base"]) == (["sim", "x", "-m", "base"],
                                                                     "pio")
    assert _pop_isa(["--isa=pio", "models"]) == (["models"], "pio")
    assert _pop_isa(["models"]) == (["models"], None)
    assert main(["models", "--isa", "nosuch"]) == 2


def test_a_program_by_name_or_path(tmp_path: Path) -> None:
    assert resolve_program("uart_tx") == PIO.program("uart_tx")
    assert resolve_program("uart_tx.pio") == PIO.program("uart_tx")
    f = tmp_path / "mine.pio"
    f.write_text("")
    assert resolve_program(f) == f
    with pytest.raises(RunError, match="uart_tx"):  # the error lists the pack's programs
        resolve_program("nosuch")


def test_a_scenario_by_name() -> None:
    from piolab.packs.pio.tests import scenarios

    assert resolve_scenario("uart_tx") is scenarios.uart_tx
    assert resolve_scenario("piolab.packs.pio.tests.scenarios:uart_tx") is scenarios.uart_tx
    with pytest.raises(RunError, match="spi_master"):
        resolve_scenario("nosuch")


def test_device_specs() -> None:
    k, o = parse("uart:baud=9600,send=0x55,0x41,parity=even")
    assert k.name == "uart" and o["baud"] == 9600 and o["send"] == (0x55, 0x41)
    assert o["parity"] == "even" and o["ppm"] == 0.0
    assert parse("uart_partner")[0].name == "uart"  # the long names still work
    assert parse("i2c:addr=0x50")[1] == {"addr": 0x50, "stretch": 0}
    assert parse("uart", {"uart": {"baud": 1200}})[1]["baud"] == 1200  # [tool.piolab.devices]
    assert parse("uart:baud=300", {"uart": {"baud": 1200}})[1]["baud"] == 300  # CLI wins
    for bad, match in [("can", "unknown device"), ("spi:speed=1", "no option 'speed'"),
                       ("spi:mode=x", "mode"), ("spi:3", "expected key=value")]:
        with pytest.raises(DeviceSpecError, match=match):
            parse(bad)


def test_sim_runs_the_engine_by_default(capsys: pytest.CaptureFixture[str],
                                        tmp_path: Path) -> None:
    vcd = tmp_path / "w.vcd"
    assert main(["sim", "uart_tx", "-d", "uart:baud=115200", "--host-tx", "0x41",
                 "--cycles", "8000", "--vcd", str(vcd)]) == 0
    out = capsys.readouterr()
    assert "stand-in" not in out.err
    assert "uart_partner" in out.out and vcd.is_file()


def test_sim_errors_are_one_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["sim", "uart_tx", "-m", "nope"]) == 2
    assert "has no model 'nope' (models: base)" in capsys.readouterr().err
    assert main(["sim", "uart_tx", "-d", "uart:speed=1"]) == 2
    assert "no option 'speed'" in capsys.readouterr().err


def test_bare_vcd_goes_to_the_out_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.piolab]\nout = "o"\n')
    monkeypatch.chdir(tmp_path)
    config.clear_cache()
    try:
        assert RunOptions(vcd="").vcd_path("uart_tx") == tmp_path / "o" / "uart_tx.vcd"
        assert RunOptions().vcd_path("uart_tx") is None
        assert main(["asm", "uart_tx", "--listing"]) == 0
        assert (tmp_path / "o" / "uart_tx.json").is_file()
        assert (tmp_path / "o" / "uart_tx.lst").is_file()
    finally:
        config.clear_cache()


def test_launch_configuration_is_the_same_run() -> None:
    o = RunOptions.from_launch({"program": "uart_tx", "devices": "uart:baud=9600",
                                "hostTx": ["0x41", "0x42"], "model": "base", "cycles": 10})
    assert o.devices == ["uart:baud=9600"] and o.host_tx == [0x41, 0x42]
    assert o.model == "base" and o.cycles == 10
    sc = o.factory()()
    assert sc.max_cycles == 10 and [a.device.name for a in sc.devices] == ["uart_partner"]


def test_models_lists_every_pack_model(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["models"]) == 0
    out = capsys.readouterr().out
    assert "pio *" in out and "piolab.packs.pio.models.base:Engine" in out


def test_scenarios_package_with_reexports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pack's `scenarios` may be a package whose __init__ re-exports its modules'
    factories; a name is found once, and helpers that build no Scenario are not listed."""
    import dataclasses
    import sys

    import piolab.packs as packs

    pkg = tmp_path / "scn_pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .uart import loop\n")
    (pkg / "uart.py").write_text(
        "from piolab.bench import Scenario\n\n"
        "def helper() -> int:\n    return 1\n\n"
        "def loop() -> Scenario:\n    raise NotImplementedError\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    pack = dataclasses.replace(PIO, scenarios="scn_pkg")
    monkeypatch.setattr(packs, "get", lambda name: pack)
    try:
        from piolab.run import scenario_names

        assert resolve_scenario("loop").__module__ == "scn_pkg.uart"
        assert scenario_names("scn_pkg") == ["loop"]
        with pytest.raises(RunError, match=r"\(loop\)"):
            resolve_scenario("helper")
    finally:
        for m in [m for m in sys.modules if m.startswith("scn_pkg")]:
            del sys.modules[m]

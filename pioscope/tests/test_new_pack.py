"""`pioscope new-pack`: the scaffold assembles, runs and matches its own yaml."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

import pioscope.packs as packs_mod
from pioscope.asm import assemble_file
from pioscope.bench.pads import PadIn
from pioscope.cli import main
from pioscope.isa.check import check_pack
from pioscope.scripts.new_pack import scaffold


def test_scaffold_writes_a_project(tmp_path: Path) -> None:
    root = scaffold("demo", tmp_path)
    assert root == tmp_path / "demo"
    for rel in ("pyproject.toml", "demo_isa/pack.py", "demo_isa/isa.yaml", "demo_isa/model.py",
                "demo_isa/demand.py", "demo_isa/programs/blink.demo",
                "demo_isa/arch/cost.yaml", "demo_isa/arch/architectures.yaml",
                "demo_isa/arch/workloads.yaml", "tests/conftest.py", "tests/test_pack.py"):
        assert (root / rel).is_file(), rel
    assert 'demo = "demo_isa.pack:PACK"' in (root / "pyproject.toml").read_text()
    with pytest.raises(FileExistsError):
        scaffold("demo", tmp_path)


@pytest.mark.parametrize("src, line", [
    ("../pioscope", 'pioscope = { path = "../pioscope", editable = true }'),
    ("dist/pioscope-0.2.0-py3-none-any.whl",
     'pioscope = { path = "dist/pioscope-0.2.0-py3-none-any.whl" }'),
    ("https://github.com/o/pioscope", 'pioscope = { git = "https://github.com/o/pioscope" }'),
])
def test_scaffold_pioscope_source(tmp_path: Path, src: str, line: str) -> None:
    assert line in (scaffold("src", tmp_path, src) / "pyproject.toml").read_text()


@pytest.mark.parametrize("bad", ["Demo", "1abc", "a-b", ""])
def test_scaffold_rejects_bad_names(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        scaffold(bad, tmp_path)


def test_cli_new_pack(tmp_path: Path) -> None:
    assert main(["new-pack", "viacli", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "viacli" / "viacli_isa" / "isa.yaml").is_file()
    assert main(["new-pack", "viacli", "--out", str(tmp_path)]) == 1  # already there


def test_generated_pack_assembles_and_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                           capsys: pytest.CaptureFixture[str]) -> None:
    root = scaffold("gen", tmp_path)
    monkeypatch.syspath_prepend(str(root))
    pack = importlib.import_module("gen_isa.pack").PACK
    monkeypatch.setattr(packs_mod, "packs", lambda: {pack.name: pack})
    try:
        with packs_mod.use("gen"):
            model = importlib.import_module("gen_isa.model")
            bad = [c.report() for c in check_pack(pack) if not c.ok]
            assert not bad, "\n".join(bad)
            image = assemble_file(pack.program("blink"))
            dut = model.Engine()
            dut.load(image)
            dut.reset()
            levels = [dut.step(PadIn()).uo & 1 for _ in range(24)]
            assert levels == [1] * 8 + [0] * 8 + [1] * 8
    finally:
        for m in [m for m in sys.modules if m.startswith("gen_isa")]:
            del sys.modules[m]


def test_generated_pages_are_named_after_the_isa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pioscope.arch import viz

    root = scaffold("zed", tmp_path)
    monkeypatch.syspath_prepend(str(root))
    pack = importlib.import_module("zed_isa.pack").PACK
    monkeypatch.setattr(packs_mod, "packs", lambda: {pack.name: pack})
    try:
        with packs_mod.use("zed"):
            page = viz.render({"archs": [], "workloads": []})
        assert "<title>ZED architecture study</title>" in page
        assert "PIOScope architecture study" not in page
    finally:
        for m in [m for m in sys.modules if m.startswith("zed_isa")]:
            del sys.modules[m]

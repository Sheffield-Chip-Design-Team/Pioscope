"""`piolab isa-check`: the shared ISA consistency checks report gaps, and pass on PIO."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from piolab.isa import check
from piolab.isa.check import check_pack
from piolab.packs import IsaPack, get

PIO = get("pio")

BROKEN_MODEL = '''
from piolab.packs.pio.models.base import HANDLERS as _PIO

HANDLERS = {k: v for k, v in _PIO.items() if k != "nop"}
HANDLERS["frob"] = _PIO["jmp"]
Engine = object
'''


@pytest.fixture
def broken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IsaPack:
    """The PIO yaml with a model that lacks `nop` and has a handler for no instruction."""
    (tmp_path / "broken_model.py").write_text(BROKEN_MODEL)
    monkeypatch.syspath_prepend(str(tmp_path))
    pack_dir = tmp_path / "broken"
    (pack_dir / "programs").mkdir(parents=True)
    shutil.copy(PIO.spec, pack_dir / "isa.yaml")
    (pack_dir / "programs" / "bad.pio").write_text(".program bad\n    frob 1\n")
    return IsaPack(name="pio", title="broken", spec=pack_dir / "isa.yaml",
                   programs=pack_dir / "programs", arch=PIO.arch,
                   models={"base": "broken_model:Engine"}, demand=PIO.demand,
                   docs=tmp_path / "docs")


def by_name(pack: IsaPack) -> dict[str, check.Check]:
    return {c.name: c for c in check_pack(pack)}


def test_pio_passes_every_check() -> None:
    bad = [c.report() for c in check_pack(PIO) if not c.ok]
    assert not bad, "\n".join(bad)


def test_reports_missing_and_extra_handlers(broken: IsaPack) -> None:
    c = by_name(broken)["instruction handlers"]
    assert not c.ok
    assert any("no handler: nop" in d for d in c.details)
    assert any("for no instruction in the yaml: frob" in d for d in c.details)


def test_reports_programs_that_do_not_assemble(broken: IsaPack) -> None:
    c = by_name(broken)["programs assemble"]
    assert not c.ok and "bad.pio" in c.details[0]


def test_docs_outside_the_pack_repo_are_skipped(broken: IsaPack) -> None:
    assert by_name(broken)["generated docs"].skipped


def test_a_bad_spec_fails_and_skips_the_rest(tmp_path: Path, broken: IsaPack) -> None:
    broken.spec.write_text("instructions: [")
    checks = check_pack(broken)
    assert not checks[0].ok and checks[0].name == "spec"
    assert all(c.skipped for c in checks[1:])


def test_used_pattern_finds_statements_not_words() -> None:
    pat = check.used_pattern("set")
    assert pat.search("loop: set x, 1")
    assert pat.search('engine("pull\\nset x, 5")')
    assert not pat.search("# reset the core")


def test_command_exit_codes(broken: IsaPack, monkeypatch: pytest.MonkeyPatch,
                            capsys: pytest.CaptureFixture[str]) -> None:
    assert check.main([]) == 0  # the active pack in this repo: pio
    monkeypatch.setattr(check, "active", lambda: broken)
    assert check.main(["-v"]) == 1
    out = capsys.readouterr().out
    assert "FAIL  instruction handlers" in out and "pio: 3 failed" in out

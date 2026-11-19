"""`[tool.piolab]`: the project defaults, and where they win or lose."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from piolab import config
from piolab.packs import ENV, active, get, use


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A project directory to write a pyproject.toml into; the cwd is a subdirectory, so
    the file is found by walking up."""
    sub = tmp_path / "src" / "deep"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    monkeypatch.delenv(ENV, raising=False)
    config.clear_cache()
    yield tmp_path
    config.clear_cache()


def write(root: Path, table: str) -> None:
    (root / "pyproject.toml").write_text(f'[project]\nname = "x"\n\n[tool.piolab]\n{table}\n')
    config.clear_cache()


def test_no_table_is_all_defaults(project: Path) -> None:
    (project / "pyproject.toml").write_text('[project]\nname = "x"\n')
    cfg = config.load()
    assert cfg.pack is None and cfg.model is None and cfg.cycles == 100_000
    assert cfg.out == project / "build" and cfg.root == project


def test_fields_and_relative_paths(project: Path) -> None:
    write(project, 'pack = "pio"\nmodel = "base"\ncycles = 500\nout = "o"\ndocs = "d"\n'
                   '[tool.piolab.devices]\nuart = { baud = 9600 }')
    cfg = config.load()
    assert (cfg.pack, cfg.model, cfg.cycles) == ("pio", "base", 500)
    assert cfg.out == project / "o" and cfg.docs == project / "d"
    assert cfg.devices == {"uart": {"baud": 9600}}
    assert get("pio").docs_dir == project / "d" / "pio"


@pytest.mark.parametrize("table, match", [
    ('pakc = "pio"', "unknown .* pakc .*valid: pack"),
    ("cycles = 0", "positive integer"),
    ('devices = { uart = 1 }', "maps a device name"),
])
def test_bad_tables_are_reported(project: Path, table: str, match: str) -> None:
    write(project, table)
    with pytest.raises(config.ConfigError, match=match):
        config.load()


def test_pack_precedence(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(project, 'pack = "nosuch"')
    with pytest.raises(KeyError, match="nosuch"):
        active()  # the table is used ...
    monkeypatch.setenv(ENV, "pio")
    assert active().name == "pio"  # ... but $PIOLAB_ISA wins over it
    monkeypatch.delenv(ENV)
    with use("pio"):
        assert active().name == "pio"  # and so does --isa / use()


def test_model_default_comes_from_the_table_when_the_pack_has_it(project: Path) -> None:
    pio = get("pio")
    write(project, 'model = "base"')
    assert pio.model_name() == "base"
    write(project, 'model = "only_in_another_pack"')
    assert pio.model_name() == pio.default_model  # another pack's model: not an error here

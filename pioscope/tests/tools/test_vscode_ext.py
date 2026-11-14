"""The VS Code extension (pioscope/vscode): generated grammar, manifest and adapter."""

import inspect
import json
import zipfile
from pathlib import Path

from pioscope.debug import dap
from pioscope.vscode import build


def test_grammar_covers_every_pack() -> None:
    g = json.loads(build.render_grammar())
    instr = next(p for p in g["patterns"] if p["name"].startswith("keyword.other.instruction"))
    for name in ("jmp", "wait", "irq", "pull", "push"):
        assert name in instr["match"]


def test_vsix_has_the_manifest_grammar_and_every_pack_suffix(tmp_path: Path) -> None:
    out = build.vsix(tmp_path / "x.vsix")
    z = zipfile.ZipFile(out)
    names = z.namelist()
    assert {"extension.vsixmanifest", "extension/package.json",
            f"extension/{build.GRAMMAR}"} <= set(names)
    pkg = json.loads(z.read("extension/package.json"))
    assert pkg["contributes"]["languages"][0]["extensions"] == build.source_extensions()
    assert ".pio" in build.source_extensions()


def test_launch_attributes_match_the_adapter() -> None:
    pkg = json.loads((build.HERE / "package.json").read_text())
    dbg = pkg["contributes"]["debuggers"][0]
    assert dbg["type"] == "pioscope"
    attrs = set(dbg["configurationAttributes"]["launch"]["properties"])
    src = (inspect.getsource(dap.factory_from_launch) + inspect.getsource(dap.dut_from_launch)
           + inspect.getsource(dap.DapServer.req_launch))
    for a in attrs - {"cwd"}:  # cwd is used by the extension itself
        assert f'"{a}"' in src, f"launch attribute '{a}' is not read by the adapter"


def test_cli_builds_the_extension(tmp_path: Path) -> None:
    from pioscope.cli import main

    assert main(["vscode", "--out", str(tmp_path)]) == 0
    assert list(tmp_path.glob("*.vsix"))

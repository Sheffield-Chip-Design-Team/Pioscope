"""The VS Code extension (tools/vscode-pioscope): generated grammar, manifest and adapter."""

import json
from typing import Any


def _ext() -> Any:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "tools" / "vscode-pioscope" / "build.py"
    spec = importlib.util.spec_from_file_location("vscode_ink_build", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extension_grammar_is_current() -> None:
    b = _ext()
    assert b.GRAMMAR.read_text() == b.render_grammar(), (
        "tools/vscode-pioscope/pioscope.tmLanguage.json is stale: run "
        "`uv run python tools/vscode-pioscope/build.py`")


def test_extension_launch_attributes_match_the_adapter(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import inspect
    import zipfile

    from pioscope.debug import dap

    b = _ext()
    pkg = json.loads((b.HERE / "package.json").read_text())
    dbg = pkg["contributes"]["debuggers"][0]
    assert dbg["type"] == "pioscope"
    attrs = set(dbg["configurationAttributes"]["launch"]["properties"])
    src = (inspect.getsource(dap.factory_from_launch) + inspect.getsource(dap.dut_from_launch)
           + inspect.getsource(dap.DapServer.req_launch))
    for a in attrs - {"cwd"}:  # cwd is used by the extension itself
        assert f'"{a}"' in src, f"launch attribute '{a}' is not read by the adapter"
    out = b.vsix(tmp_path / "x.vsix")
    names = zipfile.ZipFile(out).namelist()
    assert "extension.vsixmanifest" in names and "extension/package.json" in names




def test_extension_opens_every_pack_source_suffix() -> None:
    b = _ext()
    pkg = json.loads((b.HERE / "package.json").read_text())
    assert pkg["contributes"]["languages"][0]["extensions"] == b.source_extensions(), (
        "run `uv run python tools/vscode-pioscope/build.py`")

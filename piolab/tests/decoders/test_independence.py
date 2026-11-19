"""Decoders must not share code with the bench, devices, engine or assembler."""

import ast
import pathlib

DECODERS = pathlib.Path(__file__).resolve().parents[3] / "sim" / "decoders"
ALLOWED = {"piolab.wave", "piolab.decoders"}


def test_decoders_import_only_wave() -> None:
    bad = []
    for py in DECODERS.glob("*.py"):
        for node in ast.walk(ast.parse(py.read_text())):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import within piolab.decoders
                    continue
                mods = [node.module or ""]
            for m in mods:
                ours = m == "sim" or m.startswith("sim.")
                if ours and not any(m == a or m.startswith(a + ".") for a in ALLOWED):
                    bad.append(f"{py.name}: {m}")
    assert not bad, bad

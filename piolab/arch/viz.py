"""Render <pack docs>/arch-study.html: an interactive, self-contained view of the study.

The data is `piolab.arch.export.study_data(...)`, embedded once as JSON; the page's
JavaScript only draws it (no model logic), so it always matches arch-study.md.
One file, no external resources, light and dark themes. The page template
(HTML, CSS and the drawing script) is piolab/arch/viz_page.html.

    uv run piolab arch --write
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def out_path() -> Path:
    from piolab.packs import active

    return active().docs_dir / "arch-study.html"


#: The page: HTML, CSS and the drawing script, with a __DATA__ placeholder for the JSON.
TEMPLATE = Path(__file__).with_name("viz_page.html")


def render(data: dict[str, Any]) -> str:
    blob = json.dumps(data, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    page = TEMPLATE.read_text()
    if page.count("__DATA__") != 1:
        raise ValueError(f"{TEMPLATE.name} must contain exactly one __DATA__ placeholder")
    from piolab.arch.report import _paths, _project_docs
    from piolab.packs import active

    docs = _paths()[3]

    def ref(text: str, path: str) -> str:
        if not (_project_docs() / path).is_file():  # e.g. a downstream project
            return f"{html.escape(text)} (PIOLab docs/{html.escape(path)})"
        return f'<a href="{html.escape(docs)}/{html.escape(path)}">{html.escape(text)}</a>'

    return (page.replace("__TITLE__", html.escape(active().title))
            .replace("__GUIDE__", ref("arch-guide.md", "arch-guide.md"))
            .replace("__ADR7__", ref("ADR-0007", "open_items/0007-architecture-study.md"))
            .replace("__DATA__", blob))

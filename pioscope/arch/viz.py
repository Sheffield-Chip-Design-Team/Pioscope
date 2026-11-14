"""Render <pack docs>/arch-study.html: an interactive, self-contained view of the study.

The data is `pioscope.arch.export.study_data(...)`, embedded once as JSON; the page's
JavaScript only draws it (no model logic), so it always matches arch-study.md.
One file, no external resources, light and dark themes. The page template
(HTML, CSS and the drawing script) is pioscope/arch/viz_page.html.

    uv run pioscope arch --write
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def out_path() -> Path:
    from pioscope.packs import active

    return active().docs_dir / "arch-study.html"


#: The page: HTML, CSS and the drawing script, with a __DATA__ placeholder for the JSON.
TEMPLATE = Path(__file__).with_name("viz_page.html")


def render(data: dict[str, Any]) -> str:
    blob = json.dumps(data, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    page = TEMPLATE.read_text()
    if page.count("__DATA__") != 1:
        raise ValueError(f"{TEMPLATE.name} must contain exactly one __DATA__ placeholder")
    return page.replace("__DATA__", blob)

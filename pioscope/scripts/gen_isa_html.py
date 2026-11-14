"""Generate <pack docs>/isa-encoding.html: a self-contained page of the instruction encoding.

Same content as isa-encoding.adoc, from the same single-source layout (the ISA's
encoder module, `instr_layout`), but with the field diagrams drawn as inline SVG
by this script, so it needs no asciidoctor, WaveDrom or network access. One file,
no external resources; light and dark themes.

    uv run pioscope isa-docs
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from pioscope.asm.encode import Slot, _clog2, encoder_for, type_bits
from pioscope.asm.operands import syntax
from pioscope.image import IrInstr, ProgramImage
from pioscope.isa import InstrSpec, Isa, default_isa
from pioscope.packs import active

from .gen_isa_encoding import (
    _bits,
    _examples,
    _rule,
    _spec_path,
    by_opcode,
    code_width,
    enum_codes,
    structural,
    tier_groups,
)


def out_path() -> Path:
    return active().docs / "isa-encoding.html"

Examples = dict[str, tuple[ProgramImage, IrInstr, int]]

#: Colour class per encoding field, by position (f0..f5); set by render() for its ISA.
FIELD_CLASS: dict[str, str] = {}

E = html.escape


# ---- inline markup ------------------------------------------------------------------------


def _inline(text: str) -> str:
    """The small AsciiDoc subset `_rule` emits -> HTML."""
    out = E(text)
    out = re.sub(r"&lt;&lt;([\w.:-]+),([^&]+?)&gt;&gt;",
                 lambda m: f'<a href="#{m.group(1)}">{m.group(2)}</a>', out)
    out = re.sub(r"&lt;&lt;([\w.:-]+)&gt;&gt;", lambda m: f'<a href="#{m.group(1)}">'
                 f'{m.group(1).replace("-", " ")}</a>', out)
    out = re.sub(r"`\+(.+?)\+`", r"<code>\1</code>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    return out


def _slug(name: str) -> str:
    return "instr-" + name.replace(".", "-")


# ---- SVG field diagram ----------------------------------------------------------------------

CELL = 26
PAD = 6
TOP = 18  # bit-number row
BOX = 58
BOTTOM = 20


def _label_fits(label: str, width_px: float) -> bool:
    return len(label) * 6.8 <= width_px - 6


def svg_diagram(slots: list[Slot], word_bits: int, values: dict[int, int] | None = None,
                title: str = "") -> str:
    """One instruction word as an SVG bit-field figure, MSB on the left."""
    w = word_bits * CELL + 2 * PAD
    h = TOP + BOX + BOTTOM + 4
    parts = [f'<svg class="bitfield" viewBox="0 0 {w} {h}" role="img" '
             f'aria-label="{E(title)} bit layout">']
    for i, s in enumerate(slots):
        x = PAD + (word_bits - 1 - s.hi) * CELL
        sw = s.width * CELL
        reserved = s.kind == "reserved" or (s.fixed == 0 and s.kind != "opcode")
        cls = "slot " + ("reserved" if reserved else FIELD_CLASS.get(s.field, "f2"))
        if s.overflow:
            cls += " overflow"
        tip = f"{s.label} [{s.hi}:{s.lo}] {s.width} bit{'s' if s.width != 1 else ''}"
        parts.append(f'<g class="{cls}"><title>{E(tip)}</title>')
        parts.append(f'<rect x="{x}" y="{TOP}" width="{sw}" height="{BOX}" rx="3"/>')
        for b in range(1, s.width):  # per-bit ticks
            tx = x + b * CELL
            parts.append(f'<line class="tick" x1="{tx}" y1="{TOP}" x2="{tx}" y2="{TOP + 7}"/>'
                         f'<line class="tick" x1="{tx}" y1="{TOP + BOX - 7}" x2="{tx}" '
                         f'y2="{TOP + BOX}"/>')
        label = "" if s.label == "-" else s.label
        cx, cy = x + sw / 2, TOP + BOX / 2
        if label:
            if _label_fits(label, sw):
                parts.append(f'<text class="lbl" x="{cx}" y="{cy + 4}">{E(label)}</text>')
            else:
                short = label.split(".")[-1]
                if not _label_fits(short, BOX):
                    short = short[: max(1, int((BOX - 6) / 6.8)) - 1] + "…"
                parts.append(f'<text class="lbl" x="{cx}" y="{cy}" '
                             f'transform="rotate(-90 {cx} {cy})" dy="4">{E(short)}</text>')
        # bit numbers
        if s.width == 1:
            parts.append(f'<text class="num" x="{cx}" y="{TOP - 5}">{s.hi}</text>')
        else:
            parts.append(f'<text class="num" x="{x + CELL / 2}" y="{TOP - 5}">{s.hi}</text>'
                         f'<text class="num" x="{x + sw - CELL / 2}" y="{TOP - 5}">{s.lo}</text>')
        # bottom row
        by = TOP + BOX + 15
        if values is not None:
            bits = _bits(values[i], s.width)
            for k, ch in enumerate(bits):
                parts.append(f'<text class="bit" x="{x + k * CELL + CELL / 2}" y="{by}">'
                             f'{ch}</text>')
        elif s.fixed is not None:
            bits = _bits(s.fixed, s.width)
            for k, ch in enumerate(bits):
                parts.append(f'<text class="bit fixed" x="{x + k * CELL + CELL / 2}" '
                             f'y="{by}">{ch}</text>')
        elif s.width > 1:  # 1-bit fields: the bit number above says it all, and a "1"
            # here would read like a bit value
            parts.append(f'<text class="width" x="{cx}" y="{by}">{s.width}</text>')
        parts.append("</g>")
    parts.append("</svg>")
    return "".join(parts)


# ---- sections ---------------------------------------------------------------------------------


def _operand_rows(slots: list[Slot], isa: Isa, imem: int) -> str:
    rows = []
    for s in slots:
        rng = f"{s.hi}" if s.hi == s.lo else f"{s.hi}:{s.lo}"
        reserved = s.kind == "reserved" or (s.fixed == 0 and s.kind != "opcode")
        dot = "reserved" if reserved else FIELD_CLASS.get(s.field, "f2")
        rows.append(f'<tr><td><span class="dot {dot}"></span><code>{E(s.label)}</code></td>'
                    f"<td>{rng}</td><td>{s.width}</td><td>{_inline(_rule(s, isa, imem))}</td></tr>")
    return ('<table class="ops"><thead><tr><th>Slot</th><th>Bits</th><th>Width</th>'
            "<th>Encoding</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _instr_card(spec: InstrSpec, isa: Isa, imem: int, examples: Examples) -> str:
    enc = encoder_for(isa)
    slots = enc.instr_layout(spec, isa, imem)
    wb = isa.encoding.word_bits
    mods = ", ".join(sorted(spec.modifiers)) or "none"
    ex_html = '<p class="muted">No example program uses this instruction yet.</p>'
    ex = examples.get(spec.name)
    if ex is not None:
        image, ins, word = ex
        ex_slots = enc.instr_layout(spec, isa, image.imem_size, enc.side_layout(image.config))
        vals = {i: (word >> s.lo) & ((1 << s.width) - 1) for i, s in enumerate(ex_slots)}
        digits = (wb + 3) // 4
        ex_html = (f'<div class="example"><div class="ex-head">Example · '
                   f"<code>{E(Path(ins.src).name)}</code> · <code>{E(ins.text)}</code> → "
                   f'<code class="hex">0x{word:0{digits}x}</code></div>'
                   + svg_diagram(ex_slots, wb, vals, spec.name) + "</div>")
    status = f'<span class="badge {E(spec.status)}">{E(spec.status)}</span>'
    return (
        f'<section class="card instr" id="{_slug(spec.name)}" data-name="{E(spec.name)}" '
        f'data-tier="{spec.tier}">'
        f'<header><h3><code>{E(spec.name)}</code></h3>'
        + (f'<span class="badge tier">Tier {spec.tier}</span>' if isa.tiers else "")
        + f'<span class="badge op">opcode {spec.opcode} · '
        f'{_bits(spec.opcode, isa.encoding.width("opcode"))}</span>{status}'
        f"</header>"
        f'<p class="syntax"><code>{E(syntax(spec.name, spec.operands))}</code></p>'
        f"<p>{E(spec.semantics)}</p>"
        f'<p class="muted small">Modifiers: {E(mods)}</p>'
        + svg_diagram(slots, wb, None, spec.name)
        + _operand_rows(slots, isa, imem) + ex_html + "</section>"
    )


def _legend(isa: Isa) -> str:
    items = [(FIELD_CLASS[n], n) for n, _ in isa.encoding.fields]
    items.append(("reserved", "reserved / must be 0"))
    return '<div class="legend">' + "".join(
        f'<span><span class="dot {c}"></span>{E(t)}</span>' for c, t in items) + "</div>"


def render(isa: Isa) -> str:
    enc = encoder_for(isa)
    imem = int(isa.params["imem_size"])
    wb = isa.encoding.word_bits
    ops = by_opcode(isa)
    n_ops = 1 << isa.encoding.width("opcode")
    free = [op for op in range(n_ops) if op not in ops]
    fixed_fields = structural(isa)
    FIELD_CLASS.clear()
    FIELD_CLASS.update({n: f"f{i % 6}" for i, (n, _) in enumerate(isa.encoding.fields)})
    examples: Examples = _examples()

    common, top = [], wb
    for name, width in isa.encoding.fields:
        common.append(Slot(name, top - 1, top - width, "operand", name))
        top -= width
    fmt_rows = "".join(
        f'<tr><td><span class="dot {FIELD_CLASS[s.label]}"></span><code>{s.label}</code></td>'
        f"<td>{s.hi}:{s.lo}</td><td>{s.width}</td>"
        f"<td>{_inline(isa.encoding.roles.get(s.label, ''))}</td></tr>"
        for s in common)

    tiles = []
    for op in range(n_ops):
        owners = ops.get(op, [])
        b = _bits(op, isa.encoding.width("opcode"))
        if not owners:
            tiles.append(f'<div class="tile free"><span class="opn">{op}</span>'
                         f'<span class="opb">{b}</span><span class="opname">free</span></div>')
        else:
            spec = owners[0]
            tcls = f" t{spec.tier}" if isa.tiers else ""
            names = " / ".join(E(o.name) for o in owners)
            tiles.append(f'<a class="tile{tcls}" href="#{_slug(spec.name)}">'
                         f'<span class="opn">{op}</span><span class="opb">{b}</span>'
                         f'<span class="opname">{names}</span></a>')

    # pressure heatmap
    head = "".join(f"<th>{E(n)}<br><span class='muted small'>{w}</span></th>"
                   for n, w in isa.encoding.fields)
    prow = []
    full: dict[str, list[str]] = {}
    overflow: list[str] = []
    for spec in isa.instructions.values():
        cells = []
        for f, (used, width) in enc.field_usage(spec, isa, imem).items():
            fixed = f in fixed_fields  # full by construction
            pct = 0 if width == 0 or fixed else min(100, round(100 * used / width))
            cls = ("over" if used > width else "fixedcol" if fixed
                   else "full" if used == width else "")
            if used > width:
                overflow.append(f"{spec.name}.{f}")
            elif used == width and not fixed:
                full.setdefault(f, []).append(spec.name)
            cells.append(f'<td class="heat {cls}" style="--p:{pct}%">{used}</td>')
        prow.append(f'<tr><th><a href="#{_slug(spec.name)}"><code>{E(spec.name)}</code></a></th>'
                    + "".join(cells) + "</tr>")

    content = [(f, w) for f, w in isa.encoding.fields if f not in fixed_fields]
    headroom = {f: w - max(enc.field_usage(s, isa, imem)[f][0]
                           for s in isa.instructions.values()) for f, w in content}

    # nav + cards
    nav, cards = [], []
    for tier, title, specs in tier_groups(isa):
        nav.append(f'<div class="navgroup"><div class="navtitle">{E(title)}</div>' + "".join(
            f'<a href="#{_slug(s.name)}" data-name="{E(s.name)}"><code>{E(s.name)}</code></a>'
            for s in specs) + "</div>")
        cards.append(f'<h2 class="tierhead" id="tier-{tier or 0}">{E(title)}</h2>')
        cards += [_instr_card(s, isa, imem, examples) for s in specs]

    cond_rows = "".join(
        f"<tr><td>{i}</td><td><code>{_bits(i, _clog2(len(isa.conditions)))}</code></td>"
        f"<td><code>{E(c.name)}</code></td>"
        f"<td>{E(c.arg.name + f' ({type_bits(c.arg, isa, imem)} bits)') if c.arg else ''}</td>"
        f"<td>{E(c.semantics)}</td></tr>"
        for i, c in enumerate(isa.conditions.values()))

    used_types = {o.type.name: o.type for s in isa.instructions.values() for o in s.operands
                  if o.type.kind in ("enum", "pattern")}
    type_blocks = []
    for name in sorted(used_types):
        t = used_types[name]
        groups: list[str | None] = [None] if t.kind == "enum" else list(t.fields)
        inner = []
        for g in groups:
            pairs = enum_codes(t, g)
            w = code_width(pairs)
            rows = "".join(f"<tr><td>{c}</td><td><code>{_bits(c, w)}</code></td>"
                           f"<td><code>{E(v)}</code></td></tr>" for c, v in pairs)
            cap = f"<code>{E(g)}</code> · {w} bits" if g else f"{w} bits"
            inner.append(f'<table class="codes"><caption>{cap}</caption><thead><tr><th>Code</th>'
                         f"<th>Binary</th><th>Value</th></tr></thead><tbody>{rows}</tbody></table>")
        type_blocks.append(f'<div class="card typecard" id="type-{E(name.replace(":", "-"))}">'
                           f"<h4><code>{E(name)}</code></h4>{''.join(inner)}</div>")

    points = [
        "<b>Overflow:</b> " + (", ".join(f"<code>{E(o)}</code>" for o in overflow)
                               if overflow else f"none — every instruction fits in {wb} bits."),
        *[f"<b><code>{E(f)}</code> is full</b> for "
          + ", ".join(f'<a href="#{_slug(n)}"><code>{E(n)}</code></a>' for n in names)
          + ". A new per-bit modifier there needs a wider field or a new format."
          for f, names in full.items()],
        "<b>Headroom</b> (bits left in the most tightly packed instruction): "
        + ", ".join(f"<code>{E(f)}</code> {n} of {w}" for (f, w), n in
                    zip(content, headroom.values(), strict=True)) + ".",
        f"<b>Opcodes:</b> {len(free)} of {n_ops} free.",
        *[_inline(n) for n in isa.encoding.notes],
    ]

    stats = [(str(wb), "bit words"), (str(len(isa.instructions)), "instructions"),
             (f"{len(free)}/{n_ops}", "opcodes free"), (str(imem), "imem slots")]

    pack = active()
    draft = "" if isa.encoding.strict else ' <span class="draft">DRAFT</span>'
    return PAGE.format(
        title=E(pack.title), isa_name=E(isa.name), spec=E(_spec_path()), draft=draft,
        enc_state="draft encoding" if not isa.encoding.strict else "encoding",
        version=E(isa.version),
        stats="".join(f'<div class="stat"><div class="sv">{v}</div><div class="sl">{E(lab)}'
                      f"</div></div>" for v, lab in stats),
        legend=_legend(isa),
        word_svg=svg_diagram(common, wb, None, "word format"),
        fmt_rows=fmt_rows,
        tiles="".join(tiles),
        free=len(free), n_ops=n_ops,
        pressure_head=head, pressure_rows="".join(prow),
        nav="".join(nav), cards="".join(cards),
        cond_bits=_clog2(len(isa.conditions)), cond_rows=cond_rows,
        types="".join(type_blocks),
        points="".join(f"<li>{p}</li>" for p in points),
    )


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} instruction encoding</title>
<style>
:root {{
  --bg: #f7f8fa; --surface: #ffffff; --surface-2: #f1f3f6; --text: #1b1f24;
  --muted: #5b6573; --border: #dde1e7; --accent: #2f6fde; --accent-soft: #e6eefc;
  --f0: #5b5bd6; --f1: #0b8fc7; --f2: #138a5e; --f3: #c27a00;
  --f4: #c43d8a; --f5: #7c4dcc; --over: #d23c3c;
  --hatch: #c9ced6; --fill-alpha: 0.16;
  color-scheme: light;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #0f1216; --surface: #161a20; --surface-2: #1d222a; --text: #e6e9ee;
    --muted: #9aa4b2; --border: #2a313b; --accent: #6ea0ff; --accent-soft: #1b2740;
    --f0: #8f8fff; --f1: #4cc3f5; --f2: #3fcf98; --f3: #f2b13c;
    --f4: #f06db5; --f5: #b08cf5; --over: #ff6b6b; --hatch: #3a424e; --fill-alpha: 0.22;
    color-scheme: dark;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #0f1216; --surface: #161a20; --surface-2: #1d222a; --text: #e6e9ee;
  --muted: #9aa4b2; --border: #2a313b; --accent: #6ea0ff; --accent-soft: #1b2740;
  --f0: #8f8fff; --f1: #4cc3f5; --f2: #3fcf98; --f3: #f2b13c;
  --f4: #f06db5; --f5: #b08cf5; --over: #ff6b6b; --hatch: #3a424e; --fill-alpha: 0.22;
  color-scheme: dark;
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; scroll-padding-top: 16px; }}
body {{ margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
code, .mono {{ font-family: ui-monospace, "SF Mono", "Cascadia Code", Menlo, Consolas, monospace;
  font-size: 0.92em; }}
a {{ color: var(--accent); text-decoration: none; }} a:hover {{ text-decoration: underline; }}
.layout {{ display: grid; grid-template-columns: 240px minmax(0, 1fr); max-width: 1320px;
  margin: 0 auto; }}
nav.side {{ position: sticky; top: 0; height: 100vh; overflow-y: auto; padding: 20px 14px;
  border-right: 1px solid var(--border); }}
nav.side .brand {{ font-weight: 700; font-size: 17px; margin-bottom: 4px; }}
nav.side .ver {{ color: var(--muted); font-size: 12px; margin-bottom: 14px; }}
nav.side input {{ width: 100%; padding: 7px 9px; border-radius: 7px; border: 1px solid
  var(--border); background: var(--surface); color: var(--text); font: inherit;
  font-size: 13px; margin-bottom: 12px; }}
nav.side .sec {{ display: block; padding: 3px 6px; color: var(--text); font-size: 13.5px;
  border-radius: 5px; }}
nav.side .sec:hover, nav.side .navgroup a:hover {{ background: var(--surface-2);
  text-decoration: none; }}
.navgroup {{ margin: 10px 0; }}
.navtitle {{ font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
  color: var(--muted); padding: 0 6px 4px; }}
.navgroup a {{ display: block; padding: 2px 6px 2px 12px; border-radius: 5px;
  color: var(--text); }}
.navgroup a.hidden {{ display: none; }}
main {{ padding: 28px 32px 80px; min-width: 0; }}
.hero h1 {{ margin: 0 0 6px; font-size: 28px; letter-spacing: -0.01em; }}
.hero p {{ margin: 0 0 4px; color: var(--muted); max-width: 820px; }}
.draft {{ display: inline-block; font-size: 11px; font-weight: 700; letter-spacing: .06em;
  padding: 2px 8px; border-radius: 99px; background: var(--accent-soft); color: var(--accent);
  margin-left: 8px; vertical-align: middle; }}
.stats {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 18px 0 8px; }}
.stat {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 16px; min-width: 120px; }}
.sv {{ font-size: 22px; font-weight: 700; }} .sl {{ font-size: 12px; color: var(--muted); }}
h2 {{ margin: 40px 0 12px; font-size: 21px; }}
h2.tierhead {{ border-bottom: 1px solid var(--border); padding-bottom: 6px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  padding: 18px 20px; margin: 14px 0; }}
.instr header {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
.instr h3 {{ margin: 0 6px 0 0; font-size: 19px; }}
.badge {{ font-size: 11.5px; padding: 2px 8px; border-radius: 99px; background:
  var(--surface-2); color: var(--muted); border: 1px solid var(--border); }}
.badge.draft {{ color: var(--f3); }} .badge.stable {{ color: var(--f2); }}
.syntax code {{ background: var(--surface-2); padding: 4px 8px; border-radius: 6px; }}
.muted {{ color: var(--muted); }} .small {{ font-size: 12.5px; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 14px; font-size: 13px; color: var(--muted);
  margin: 8px 0 4px; }}
.dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 3px;
  margin-right: 6px; vertical-align: -1px; border: 1.5px solid currentColor; }}
.dot.f0 {{ color: var(--f0); }} .dot.f1 {{ color: var(--f1); }}
.dot.f2 {{ color: var(--f2); }} .dot.f3 {{ color: var(--f3); }}
.dot.f4 {{ color: var(--f4); }} .dot.f5 {{ color: var(--f5); }}
.dot.reserved {{ color: var(--hatch); background: repeating-linear-gradient(45deg,
  var(--hatch) 0 2px, transparent 2px 4px); }}
svg.bitfield {{ width: 100%; height: auto; display: block; margin: 12px 0 6px;
  overflow: visible; }}
svg.bitfield rect {{ stroke-width: 1.5; }}
svg.bitfield text {{ text-anchor: middle; fill: var(--text);
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }}
svg.bitfield .lbl {{ font-size: 12px; font-weight: 600; }}
svg.bitfield .num {{ font-size: 10px; fill: var(--muted); }}
svg.bitfield .bit {{ font-size: 12px; }} svg.bitfield .bit.fixed {{ fill: var(--muted); }}
svg.bitfield .width {{ font-size: 11px; fill: var(--muted); }}
svg.bitfield .tick {{ stroke: currentColor; stroke-width: 1; opacity: .45; }}
.slot {{ color: var(--f2); }}
.slot rect {{ fill: currentColor; fill-opacity: var(--fill-alpha); stroke: currentColor; }}
.slot.f0 {{ color: var(--f0); }} .slot.f1 {{ color: var(--f1); }}
.slot.f2 {{ color: var(--f2); }} .slot.f3 {{ color: var(--f3); }}
.slot.f4 {{ color: var(--f4); }} .slot.f5 {{ color: var(--f5); }}
.slot.reserved {{ color: var(--hatch); }}
.slot.reserved rect {{ fill: url(#hatch); fill-opacity: 1; }}
.slot.overflow rect {{ stroke: var(--over); stroke-dasharray: 4 3; stroke-width: 2.5; }}
table {{ border-collapse: collapse; width: 100%; font-size: 13.5px; }}
th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border);
  vertical-align: top; }}
thead th {{ color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase;
  letter-spacing: .04em; }}
.tablewrap {{ overflow-x: auto; }}
.example {{ background: var(--surface-2); border-radius: 10px; padding: 10px 14px 4px;
  margin-top: 14px; }}
.ex-head {{ font-size: 13px; color: var(--muted); }}
.hex {{ color: var(--accent); font-weight: 600; }}
.opgrid {{ display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 8px; }}
.tile {{ display: flex; flex-direction: column; padding: 8px 10px; border-radius: 9px;
  border: 1px solid var(--border); background: var(--surface); color: var(--text);
  min-height: 70px; }}
.tile:hover {{ text-decoration: none; border-color: var(--accent); }}
.tile.free {{ border-style: dashed; color: var(--muted); background: transparent; }}
.tile.t1 {{ border-left: 4px solid var(--f0); }} .tile.t2 {{ border-left: 4px solid
  var(--f2); }} .tile.t3 {{ border-left: 4px solid var(--f5); }}
.opn {{ font-size: 11px; color: var(--muted); }} .opb {{ font-family: ui-monospace, monospace;
  font-size: 11px; color: var(--muted); }}
.opname {{ font-family: ui-monospace, monospace; font-weight: 600; margin-top: auto; }}
td.heat {{ text-align: center; font-variant-numeric: tabular-nums;
  background: color-mix(in srgb, var(--accent) calc(var(--p) * 0.35), transparent); }}
td.heat.fixedcol {{ color: var(--muted); }}
td.heat.full {{ font-weight: 700; box-shadow: inset 0 0 0 2px var(--accent); }}
td.heat.over {{ background: color-mix(in srgb, var(--over) 35%, transparent);
  font-weight: 700; }}
.typegrid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px; }}
.typecard {{ margin: 0; }} .typecard h4 {{ margin: 0 0 8px; }}
table.codes caption {{ text-align: left; font-size: 12.5px; color: var(--muted);
  padding: 4px 0; }}
ul.points li {{ margin: 6px 0; }}
.themebtn {{ margin-top: 14px; font: inherit; font-size: 12.5px; padding: 6px 10px;
  border-radius: 7px; border: 1px solid var(--border); background: var(--surface);
  color: var(--text); cursor: pointer; }}
footer {{ color: var(--muted); font-size: 12.5px; margin-top: 40px; }}
@media (max-width: 860px) {{
  .layout {{ grid-template-columns: 1fr; }}
  nav.side {{ position: static; height: auto; border-right: 0;
    border-bottom: 1px solid var(--border); }}
  main {{ padding: 20px 16px 60px; }}
  .opgrid {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
}}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse"
  patternTransform="rotate(45)"><rect width="6" height="6" fill="transparent"/>
<line x1="0" y1="0" x2="0" y2="6" stroke="var(--hatch)" stroke-width="2"/></pattern>
</defs></svg>
<div class="layout">
<nav class="side">
  <div class="brand">{title}</div>
  <div class="ver">ISA {version} · {enc_state}</div>
  <input id="filter" type="search" placeholder="Filter instructions…" aria-label="Filter">
  <a class="sec" href="#format">Word format</a>
  <a class="sec" href="#opcode-map">Opcode map</a>
  <a class="sec" href="#pressure">Encoding pressure</a>
  {nav}
  <a class="sec" href="#conditions">Conditions</a>
  <a class="sec" href="#types">Operand codes</a>
  <a class="sec" href="#open">Notes</a>
  <button class="themebtn" id="theme" type="button">Toggle theme</button>
</nav>
<main>
<div class="hero">
  <h1>{title} instruction encoding{draft}</h1>
  <p>Field-by-field encoding of {isa_name}. Generated from <code>{spec}</code> by
  <code>pioscope isa-docs</code> — do not edit.</p>
  <p class="small">Diagrams put the most significant bit on the left. Hover a field for its
  bit range. Under each field: its fixed bits, or its width in bits (multi-bit fields).</p>
</div>
<div class="stats">{stats}</div>
{legend}

<h2 id="format">Word format</h2>
<div class="card">{word_svg}
<div class="tablewrap"><table><thead><tr><th>Field</th><th>Bits</th><th>Width</th><th>Role</th>
</tr></thead><tbody>{fmt_rows}</tbody></table></div></div>

<h2 id="opcode-map">Opcode map</h2>
<p class="muted">{free} of {n_ops} opcodes free.</p>
<div class="opgrid">{tiles}</div>

<h2 id="pressure">Encoding pressure</h2>
<p class="muted">Bits each instruction uses per field. Shading = how full the field is;
outlined = exactly full; red = overflow. Fields that hold no operands (the opcode, delay and
side-set) are full by construction and shown greyed.</p>
<div class="card tablewrap"><table><thead><tr><th>Instruction</th>{pressure_head}</tr></thead>
<tbody>{pressure_rows}</tbody></table></div>

{cards}

<h2 id="conditions">Conditions</h2>
<p class="muted">Condition codes are {cond_bits} bits; conditions with an argument carry it in
the following <code>cond.arg</code> bits.</p>
<div class="card tablewrap"><table><thead><tr><th>Code</th><th>Binary</th><th>Condition</th>
<th>Argument</th><th>Meaning</th></tr></thead><tbody>{cond_rows}</tbody></table></div>

<h2 id="types">Operand codes</h2>
<div class="typegrid">{types}</div>

<h2 id="open">Notes</h2>
<div class="card"><ul class="points">{points}</ul></div>
<footer>Generated by <code>pioscope/scripts/gen_isa_html.py</code> from the same layout function the
assembler uses (the encoder's <code>instr_layout</code>).</footer>
</main>
</div>
<script>
(function () {{
  var root = document.documentElement;
  try {{ var t = localStorage.getItem("isa-theme"); if (t) root.setAttribute("data-theme", t); }}
  catch (e) {{}}
  document.getElementById("theme").addEventListener("click", function () {{
    var dark = root.getAttribute("data-theme") === "dark" || (!root.getAttribute("data-theme")
      && window.matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {{ localStorage.setItem("isa-theme", next); }} catch (e) {{}}
  }});
  var f = document.getElementById("filter");
  f.addEventListener("input", function () {{
    var q = f.value.trim().toLowerCase();
    document.querySelectorAll("section.instr").forEach(function (s) {{
      s.style.display = !q || s.dataset.name.indexOf(q) >= 0 ? "" : "none";
    }});
    document.querySelectorAll(".navgroup a").forEach(function (a) {{
      a.classList.toggle("hidden", !!q && a.dataset.name.indexOf(q) < 0);
    }});
  }});
}})();
</script>
</body>
</html>
"""


def main() -> None:
    out = out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(default_isa()))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

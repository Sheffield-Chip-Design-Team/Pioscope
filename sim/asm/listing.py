"""Human-readable listing of an assembled image."""

from __future__ import annotations

import json

from sim.image import ProgramImage


def render_listing(image: ProgramImage, word_bits: int | None = None) -> str:
    digits = ((word_bits or image.word_bits) + 3) // 4
    by_addr: dict[int, list[str]] = {}
    for name, addr in image.symbols.items():
        by_addr.setdefault(addr, []).append(name)
    wrap = image.config.get("wrap", {})
    out = [
        f"; program   {image.name}",
        f"; source    {image.source}",
        f"; isa       {image.isa_version}",
        f"; imem      {image.size}/{image.imem_size} slots",
        "",
        "addr  word" + " " * (digits - 2) + "  source",
    ]
    for ins, word in zip(image.instructions, image.words, strict=True):
        for label in by_addr.get(ins.addr, []):
            out.append(f"{'':4}  {'':{digits}}  {label}:")
        w = f"{word:0{digits}x}" if word is not None else "-" * digits
        marks = ""
        if wrap.get("target") == ins.addr:
            marks += "  <- wrap_target"
        if wrap.get("wrap") == ins.addr:
            marks += "  -> wrap"
        out.append(f"{ins.addr:02x}    {w}  {ins.text:32s}; {ins.src}{marks}")
    out += ["", "; config"]
    for k, v in image.config.items():
        out.append(f";   {k}: {json.dumps(v, sort_keys=True)}")
    if image.warnings:
        out += ["", "; warnings"] + [f";   {w}" for w in image.warnings]
    return "\n".join(out) + "\n"

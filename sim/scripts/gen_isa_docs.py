"""Generate <pack docs>/isa.md from the active pack's ISA yaml.  `uv run pioscope isa-docs`"""

from __future__ import annotations

from pathlib import Path

from sim.asm.operands import syntax
from sim.isa import Isa, default_isa
from sim.packs import active

from .gen_isa_encoding import _spec_path, tier_groups


def out_path() -> Path:
    return active().docs / "isa.md"


def render(isa: Isa) -> str:
    out = [
        f"# {active().title} ISA reference (generated)",
        "",
        f"{isa.name}. Generated from `{_spec_path()}` (version {isa.version}). Do not edit "
        "by hand: run `uv run pioscope isa-docs`. Encoding: [isa-encoding.html]"
        "(isa-encoding.html).",
        "",
    ]
    if isa.status:
        out += [f"Status: {isa.status}", ""]
    for _tier, title, specs in tier_groups(isa):
        out += [f"## {title}", "", "| Syntax | Modifiers | Status | Semantics |",
                "|---|---|---|---|"]
        for ins in specs:
            mods = ", ".join(sorted(ins.modifiers)) or "-"
            out.append(f"| `{syntax(ins.name, ins.operands)}` | {mods} | {ins.status} | "
                       f"{ins.semantics} |")
        out.append("")
    out += ["## Conditions", "", "| Condition | Semantics |", "|---|---|"]
    for c in isa.conditions.values():
        name = f"{c.name} <{c.arg.name}>" if c.arg else c.name
        out.append(f"| `{name}` | {c.semantics} |")
    for title, harness in (("Directives", False), ("Harness directives", True)):
        ds = [d for d in isa.directives.values() if d.harness == harness]
        if not ds:
            continue
        out += ["", f"## {title}", ""]
        if harness:
            out += ["Configuration the bench needs that the ISA's own assembler does not "
                    "have (for example what a C SDK sets up).", ""]
        out += ["| Syntax | Semantics |", "|---|---|"]
        for d in ds:
            head = d.name + "<N>" if d.pattern else d.name
            out.append(f"| `{syntax(head, d.operands)}` | {d.semantics} |")
    kind = "Encoding" if isa.encoding.strict else "Draft encoding"
    out += ["", f"## {kind}", "",
            f"{isa.encoding.word_bits}-bit word, MSB first: " +
            " \\| ".join(f"{n} ({w})" for n, w in isa.encoding.fields) + ".", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="pioscope isa-docs",
        description="Regenerate isa.md, isa-encoding.adoc and isa-encoding.html in the "
                    "active ISA pack's docs directory, from its yaml.")
    ap.parse_args(argv)
    out = out_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(default_isa()))
    print(f"wrote {out}")
    from sim.scripts import gen_isa_encoding, gen_isa_html

    gen_isa_encoding.main()
    gen_isa_html.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

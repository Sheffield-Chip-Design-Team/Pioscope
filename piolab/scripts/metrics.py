"""Per-program ISA metrics (v0: static, from the assembled image).

Dynamic metrics (cycles per bit, max bit rate vs sysclk) come from scenario runs on
the engine model; until they are wired in, those columns print 'n/a'.

    uv run piolab metrics [programs...] [--markdown]   # default: the active pack's

A pack's `demand` module may define `BASELINE = {program: (reference, instructions)}`:
hand-written equivalents to compare against (checked against their source).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter

from piolab.arch.demand import units_used
from piolab.asm import assemble_file
from piolab.image import ProgramImage
from piolab.isa import load_isa
from piolab.packs import active, for_image


def metrics(image: ProgramImage) -> dict[str, object]:
    pack = for_image(image)
    isa = load_isa(pack.spec)
    row: dict[str, object] = {"program": image.name, "imem": f"{image.size}/{image.imem_size}"}
    tiers = sorted({s.tier for s in isa.instructions.values()})
    if len(tiers) > 1:
        used = Counter(isa.instructions[i.name].tier for i in image.instructions)
        row["tiers " + "/".join(map(str, tiers))] = "/".join(str(used[t]) for t in tiers)
    row["config_bytes"] = len(json.dumps(image.config, separators=(",", ":")))
    row["units"] = ",".join(units_used(image)) or "-"
    row["encodable"] = "yes" if image.encodable else "NO"
    baseline = getattr(pack.demand_module(), "BASELINE", {})
    if baseline:
        ref = baseline.get(image.name.removesuffix("_pio"))
        row["baseline"] = f"{ref[1]} ({ref[0]})" if ref else "-"
    row["cycles/bit"] = "n/a"
    row["max_rate@50MHz"] = "n/a"
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="piolab metrics",
                                 description="Per-program static ISA metrics.")
    ap.add_argument("sources", nargs="*",
                    help="programs: paths or program names (default: all of the active ISA "
                    "pack's)")
    ap.add_argument("--markdown", action="store_true")
    a = ap.parse_args(argv)
    pack = active()
    from piolab.run import resolve_program

    srcs = ([resolve_program(s) for s in a.sources] if a.sources
            else sorted(pack.programs.glob(f"*{pack.ext}")))
    rows = [metrics(assemble_file(p)) for p in srcs]
    cols = list(rows[0])
    if a.markdown:
        print("| " + " | ".join(cols) + " |")
        print("|" + "---|" * len(cols))
        for r in rows:
            print("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    else:
        widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
        print("  ".join(c.ljust(widths[c]) for c in cols))
        for r in rows:
            print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

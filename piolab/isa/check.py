"""Check an ISA pack's yaml against everything that consumes it.  `piolab isa-check`

Agreements #1 and #6 as one reusable set of checks, for any pack:

- `spec`: the yaml loads and validates;
- `instruction handlers`: the engine model has exactly one handler per instruction (its
  module's `HANDLERS` table, built with `piolab.dut.core.registry()`);
- `directive handlers`: every directive has an assembler handler;
- `semantics text`: every instruction and directive has spec text in the yaml;
- `programs assemble`: every program of the pack assembles;
- `instructions used`: every instruction is used by a program or a directed test;
- `generated docs`: isa.md and isa-encoding.adoc/.html are current (skipped where absent).

The pack tests call `check_pack` too, so the command and the tests check the same things:

    @pytest.mark.parametrize("check", check_pack(get("mypack")), ids=lambda c: c.name)
    def test_isa_check(check: Check) -> None:
        assert check.ok or check.skipped, check.report()
"""

from __future__ import annotations

import argparse
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from piolab.isa import Isa, load_isa
from piolab.packs import IsaPack, active, display_path, packs, use


@dataclass
class Check:
    name: str
    ok: bool
    details: list[str] = field(default_factory=list)
    skipped: bool = False

    @property
    def status(self) -> str:
        return "skip" if self.skipped else ("ok" if self.ok else "FAIL")

    def report(self) -> str:
        return "\n".join([f"{self.status:4s}  {self.name}", *(f"      {d}" for d in self.details)])


def _passed(name: str, *details: str) -> Check:
    return Check(name, True, list(details))


def _failed(name: str, *details: str) -> Check:
    return Check(name, False, list(details))


def _skipped(name: str, *details: str) -> Check:
    return Check(name, True, list(details), skipped=True)


# ---- the checks -----------------------------------------------------------------------------


def model_handlers(pack: IsaPack, model: str | None = None) -> dict[str, object] | None:
    """A model's instruction handler table (default: the pack's default model), or None
    if its module has none."""
    table = getattr(pack.model_module(model or pack.default_model), "HANDLERS", None)
    return dict(table) if isinstance(table, dict) else None


def check_instruction_handlers(pack: IsaPack, isa: Isa) -> Check:
    """Every model of the pack has exactly one handler per instruction."""
    name = "instruction handlers"
    ok, skipped, details = True, 0, []
    for model in pack.models:
        table = model_handlers(pack, model)
        if table is None:
            skipped += 1
            details.append(f"{model}: {pack.models[model].partition(':')[0]} has no HANDLERS "
                           "table (export the one registry() builds to have it checked)")
            continue
        missing = sorted(set(isa.instructions) - set(table))
        extra = sorted(set(table) - set(isa.instructions))
        if missing:
            details.append(f"{model}: in the yaml but no handler: {', '.join(missing)}")
        if extra:
            details.append(f"{model}: handler for no instruction in the yaml: {', '.join(extra)}")
        if missing or extra:
            ok = False
        else:
            details.append(f"{model}: {len(table)} handlers for {len(isa.instructions)} "
                           "instructions")
    if not ok:
        return _failed(name, *details)
    if skipped == len(pack.models):
        return _skipped(name, *details)
    return _passed(name, *details)


def check_directive_handlers(pack: IsaPack, isa: Isa) -> Check:
    from piolab.asm.directives import handlers

    missing = sorted(set(isa.directives) - set(handlers(isa)))
    if missing:
        return _failed("directive handlers", f"no handler: {', '.join(missing)}")
    return _passed("directive handlers", f"{len(isa.directives)} directives")


def check_semantics_text(pack: IsaPack, isa: Isa) -> Check:
    empty = [n for n, s in isa.instructions.items() if not s.semantics.strip()]
    empty += [n for n, d in isa.directives.items() if not d.semantics.strip()]
    if empty:
        return _failed("semantics text", f"no semantics in the yaml: {', '.join(empty)}")
    return _passed("semantics text")


def programs(pack: IsaPack) -> list[Path]:
    return sorted(pack.programs.glob(f"*{pack.ext}")) if pack.programs.is_dir() else []


def check_programs_assemble(pack: IsaPack, isa: Isa) -> Check:
    from piolab.asm import assemble_file

    name = "programs assemble"
    progs = programs(pack)
    if not progs:
        return _skipped(name, f"no *{pack.ext} programs in {display_path(pack.programs)}")
    bad = []
    for p in progs:
        try:
            assemble_file(p, isa)
        except Exception as e:  # AsmError / AsmErrors / EncodeError: report them all
            first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
            bad.append(f"{display_path(p)}: {first}")
    if bad:
        return _failed(name, *bad)
    return _passed(name, f"{len(progs)} programs")


def corpus_files(pack: IsaPack) -> list[Path]:
    """Where instructions must be used: the pack's programs and its directed tests, in the
    pack's package (`<pkg>/tests`) or at its project root (`<pkg>/../tests`)."""
    pkg = pack.programs.parent
    tests = [d for d in (pkg / "tests", pkg.parent / "tests") if d.is_dir()]
    return [*programs(pack), *(p for d in tests for p in sorted(d.rglob("*.py")))]


def used_pattern(name: str) -> re.Pattern[str]:
    """`name` as a statement: at a line start, after an escaped newline in a Python string,
    or at the start of a string literal, optionally after a label."""
    return re.compile(rf"""(?m)(^|\\n|["'])\s*(\w+:)?\s*{re.escape(name)}\b""")


def unused_instructions(pack: IsaPack, isa: Isa) -> list[str]:
    corpus = "\n".join(p.read_text() for p in corpus_files(pack))
    return [n for n in isa.instructions if not used_pattern(n).search(corpus)]


def check_instructions_used(pack: IsaPack, isa: Isa) -> Check:
    name = "instructions used"
    if not corpus_files(pack):
        return _skipped(name, "no programs or tests to look in")
    missing = unused_instructions(pack, isa)
    if missing:
        return _failed(name, f"no program or directed test uses: {', '.join(missing)}")
    return _passed(name, f"all {len(isa.instructions)} in programs/ or tests/")


def check_generated_docs(pack: IsaPack, isa: Isa) -> Check:
    from piolab.scripts import gen_isa_docs, gen_isa_encoding, gen_isa_html

    name = "generated docs"
    # The generators name their files after the active pack; the files are in this pack's docs.
    with use(pack.name):
        docs = {m: pack.docs_dir / m.out_path().name
                for m in (gen_isa_docs, gen_isa_encoding, gen_isa_html)}
        present = {m: p for m, p in docs.items() if p.exists()}
        if not present:
            return _skipped(name, f"none in {display_path(pack.docs_dir)} "
                                  "(run from the pack's repo to check them)")
        stale = [display_path(p) for m, p in present.items() if p.read_text() != m.render(isa)]
        absent = [display_path(p) for m, p in docs.items() if m not in present]
    if stale or absent:
        return _failed(name, *(f"stale: {s}" for s in stale), *(f"missing: {s}" for s in absent),
                       f"run `piolab --isa {pack.name} isa-docs`")
    return _passed(name, f"{len(present)} current")


CHECKS: tuple[Callable[[IsaPack, Isa], Check], ...] = (
    check_instruction_handlers,
    check_directive_handlers,
    check_semantics_text,
    check_programs_assemble,
    check_instructions_used,
    check_generated_docs,
)


def check_pack(pack: IsaPack) -> list[Check]:
    """Every check on `pack`. A check that raises is reported as failed, not raised."""
    try:
        isa = load_isa(pack.spec)
    except Exception as e:  # IsaSpecError, a yaml syntax error, a missing file
        names = ["instruction handlers", "directive handlers", "semantics text",
                 "programs assemble", "instructions used", "generated docs"]
        return [_failed("spec", str(e)), *(_skipped(n, "the spec did not load") for n in names)]
    out = [_passed("spec", f"{display_path(pack.spec)} ({len(isa.instructions)} instructions, "
                           f"{len(isa.directives)} directives)")]
    for fn in CHECKS:
        try:
            out.append(fn(pack, isa))
        except Exception as e:
            out.append(_failed(fn.__name__.removeprefix("check_").replace("_", " "),
                               f"{type(e).__name__}: {e}"))
    return out


# ---- the command ----------------------------------------------------------------------------


def instruction_table(pack: IsaPack) -> str:
    isa = load_isa(pack.spec)
    table = model_handlers(pack) or {}
    unused = set(unused_instructions(pack, isa))
    rows = [("instruction", "tier", "handler", "semantics", "used")]
    for n, s in isa.instructions.items():
        rows.append((n, str(s.tier), "yes" if n in table else "NO",
                     "yes" if s.semantics.strip() else "NO", "NO" if n in unused else "yes"))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip()
                     for r in rows)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="piolab isa-check",
        description="Check the active ISA pack's yaml against its engine model, directive "
                    "handlers, programs, directed tests and generated docs. Exits 1 on any "
                    "failure.")
    ap.add_argument("--all", action="store_true", help="check every installed pack")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="also print a per-instruction table (handler, semantics, used)")
    a = ap.parse_args(argv)
    failed = 0
    for pack in (packs().values() if a.all else [active()]):
        print(f"== {pack.name} ({pack.title})")
        checks = check_pack(pack)
        for c in checks:
            print(c.report())
        bad = [c.name for c in checks if not c.ok]
        if a.verbose and checks[0].ok:
            print()
            print(instruction_table(pack))
        print(f"-- {pack.name}: " + (f"{len(bad)} failed: {', '.join(bad)}" if bad else "all ok"))
        failed += bool(bad)
    return 1 if failed else 0

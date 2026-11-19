# ADR-0012: ISA packs

Status: accepted (2026-11-14).

## Context
The assembler, the bench, the device models, the decoders, the debugger, the trace, the VCD and the architecture study were written against one instruction set. They are mostly table-driven from its yaml, but a few places named that ISA directly:
- some directive handlers;
- the reference tables;
- the binary encoder (one fixed field layout, one unique opcode per instruction);
- the engine model the debugger imports;
- the list of units the architecture study allocates.

That made it impossible to:
- evaluate a second ISA (for example a PIO-compatible baseline) with the same tools;
- compare a second ISA against the first like-for-like;
- reuse the framework without carrying one particular ISA along.

## Decision
1. An **ISA pack** bundles everything that belongs to one instruction set. A pack is a module that defines `PACK = IsaPack(...)`: a sub-package of `piolab.packs` (the built-in PIO pack) or any installed package registered under the `piolab.packs` entry-point group (see *Consequences (packaging)*):
   - **spec:** `<pack>/isa.yaml`, the single source of truth for that ISA (CLAUDE.md §8 #1).
   - **model:** the engine model (`module:callable`), which is what `--dut engine` builds. *ADR-0015 replaces this with named `models` (one per `<pkg>/models/<name>/`) and a `default_model`.*
   - **demand:** the arch-study hooks (`units_used`, `bit_shares`, `fifos`).
   - **programs:** its protocol programs.
   - **arch:** its arch-study data (`<pack>/arch/`).
   - **docs:** where its generated documents go.
2. The yaml names its own code hooks, so a loaded `Isa` is self-describing:
   ```yaml
   hooks:
     directives: <module>   # DIRECTIVE_HANDLERS (+ optional resolve_config) for its directives
     encoder: <module>      # encode_image / decode_word / instr_layout; default: generic fields
   ```
   With a custom encoder, several instructions may share a major opcode.
3. The generic directive handlers live in `piolab.asm.directives` (`GENERIC`). Those are `.program`, `.pins`, `.side_set`, `.fifo`, `.clkdiv`, `.wrap_target`, `.wrap` and `.define`. A pack adds handlers or overrides them.
4. Names usable without a defining directive are declared generically as `builtin_refs: {<table>: [names]}`.
5. **The active pack** is `$PIOLAB_ISA` if set, otherwise the discovered pack with the lowest `rank`. Tools and tests that need a particular pack use `piolab.packs.use(name)`, and the CLI uses `--isa`. Each pack's tests live with the pack and pin it, so one `pytest` run covers every pack.
6. **Engine models** share a generic core (`piolab.dut.core`): registers, FIFOs, pins, side-set, delay, wrap, the clock divider, handler dispatch, trace, probes and `state()`, the `Unit` base and the observability contract (ADR-0010). Each pack's model subclasses that core and adds its own handlers and units.

## Consequences
- A new ISA is a new pack. None of the framework's code changes.
- Generated documents move to the pack's `docs` directory, and arch-study data moves to `<pack>/arch/`.
- The debugger, the trace and the VCD depend only on the core and the observability contract, never on one pack's model.
- The arch study's unit list comes from the pack's `cost.yaml`, so a pack with no units is valid.

## Consequences (packaging, 2026-11-14)
PIOLab is an installable library. Downstream projects (another ISA, a tapeout) depend on it instead of copying it.
- **Entry points.** `packs()` returns the built-in sub-packages plus every `piolab.packs` entry point, so a pack in another package is found once it is installed. Two packs with one name is an error.
- **Package-relative data.** A pack's spec, programs and arch data sit next to its module (`HERE = pack_dir(__file__)`), so the pack works from a wheel as well as from a checkout. The PIO pack's data is in `piolab/packs/pio/`; there is no `spec/` directory.
- **Docs under the working directory.** `IsaPack.docs` defaults to `docs/<name>` under the cwd: generated documents belong to the project being worked on, not to the installed library. Messages and documents show paths with `display_path()`.
- **The pytest plugin.** The bench fixtures and options (`bench`, `golden`, `--backend`, `--dut`, `--waves`, `--update-golden`) are `piolab.testing`, registered as `pytest11`, so a downstream project needs no conftest for them.
- **The RTL harness is package data** (`piolab/bench/rtl/`). `run_rtl` builds under the cwd and takes includes from the sources' directories.
- **The VS Code extension** is package data too. `piolab vscode` builds it for the installed packs, so the grammar and the file suffixes always match them; there is no committed grammar.
- **`piolab new-pack NAME`** scaffolds a pack project with its entry point, the minimal ISA, a model, arch data and tests. This is how a new ISA starts (docs/reference/isa-flow.md §5).
- **The Tiny Tapeout template** is its own repository (`pioscope-tt-template`). PIOLab keeps only the bench side of it.
- Generated pages are titled from the pack's `title`, never from the framework's name.

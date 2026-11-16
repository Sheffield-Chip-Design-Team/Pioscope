# Changelog

All notable changes to PIOScope. Versions follow [semantic versioning](https://semver.org);
before 1.0 the `IsaPack`, hooks and `pioscope.dut.core` APIs may still change between minor
versions.

## Unreleased

### Added
- `pioscope isa-check [--all] [-v]`: checks a pack's yaml against its engine model's `HANDLERS`
  (missing and extra), its directive handlers, semantics text, programs, directed tests and
  generated docs; exits 1 on any gap. `pioscope.isa.check.check_pack` is the same set of checks
  for a pack's tests.
- CAN: a `CanNode` device model (`pioscope.devices.can`: CAN 2.0A/B data and remote frames,
  stuffing, CRC-15, arbitration, ACK, error frames and fault confinement up to bus-off, hard
  sync and resync, per-node clock error, injected stuff/CRC/form/bit errors) and an independent
  decoder, `decode_can` (`pioscope.decoders.can`). Node↔node loopbacks: `can_link`, `can_bus`
  and `can_arbitration` in `pioscope.tests.devices.loopbacks`.

### Changed
- The PIO pack tests and the `new-pack` scaffold run their ISA coverage through `check_pack`.
  The scaffold also gains a directed test of `jmp` and `nop`, which it lacked.

## 0.2.0 (2026-11-14)

PIOScope becomes an installable library. Your ISA lives in your own package; PIOScope finds it
by entry point (ADR-0012, "Consequences (packaging)").

### Added
- ISA packs are discovered from the `pioscope.packs` entry-point group as well as the
  built-in sub-packages.
- `pioscope new-pack NAME [--out DIR] [--pioscope PATH_OR_URL]`: scaffolds a ready-to-install
  pack project (a three-instruction ISA, a model on `pioscope.dut.core`, arch-study data, a
  `blink` program, tests).
- pytest plugin (`pioscope.testing`, a `pytest11` entry point): the `bench` and `golden`
  fixtures and the `--backend`, `--dut`, `--sim`, `--rtl-engine`, `--rtl-src`, `--waves`,
  `--update-golden` options, with no conftest needed.
- `pioscope vscode [--out DIR]`: builds the VS Code extension for the installed packs (the
  grammar and file suffixes are generated from them).
- CI workflow: pytest, ruff, mypy.

### Changed
- The Python package is `pioscope` (was `sim`); the command is `pioscope`.
- Pack data (spec, programs, arch data) lives inside the package, next to the pack's module;
  `spec/` is gone. The RTL harness (`pioscope/bench/rtl/`) and the VS Code extension
  (`pioscope/vscode/`) ship as package data.
- Generated documents go to `docs/<pack>` under the working directory.
- Generated pages (arch study, ISA encoding) are titled from the pack's `title`.
- The arch study's links to ADR-0007 and `arch-guide.md` are relative to the page (they were
  broken), and plain text in a project without PIOScope's docs.
- The debugger prompt is `(pioscope)`.

### Removed
- The Tiny Tapeout project template (`src/`, `test/`, `info.yaml`, `docs/info.md`,
  `.devcontainer`, the GDS/FPGA/docs/test workflows). It is now the `pioscope-tt-template`
  repository.
- The committed VS Code grammar (generated at build time instead).

## 0.1.0

First version, as the framework inside a single project.

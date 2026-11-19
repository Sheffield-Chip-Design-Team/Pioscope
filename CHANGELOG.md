# Changelog

All notable changes to PIOLab (called PIOScope up to 0.2.0). Versions follow
[semantic versioning](https://semver.org); before 1.0 the `IsaPack`, hooks and
`piolab.dut.core` APIs may still change between minor versions.

## 0.3.0 (unreleased)

PIOScope is renamed **PIOLab**. Packs can hold several engine models, a project sets its
defaults in `pyproject.toml`, and the run commands get shorter (ADR-0015).

### Changed (breaking)
- Renamed everywhere, with no compatibility alias: the distribution and package `piolab`,
  the `piolab` command, the `piolab.packs` entry-point group, the `piolab` pytest plugin,
  `$PIOLAB_ISA` and the other `PIOLAB_*` variables, the `PIOLAB_IMEM_PATH` Verilog macro,
  `sim_build/piolab_<sim>`, the `(piolab)` debugger prompt, and in VS Code the `piolab`
  debug type, the `piolab-asm` language and the `piolab.adapterCommand/adapterArgs` settings.
- `IsaPack(model=...)` is replaced by `models={name: "module:callable"}` and `default_model`;
  `discover_models("<pkg>.models")` finds one model per sub-package.
- The PIO model moved from `piolab.dut.pio` to `piolab.packs.pio.models.base`. The PIO
  pack is the demonstration pack, with rank 1000, so an installed project pack is the
  default.
- `piolab sim` runs the engine model by default (`--dut stub` for the stand-in).
- Device options are specs: `--device uart:baud=N,send=0x55,0x41,ppm=N,bits=N,parity=P,stop=N`,
  `spi:mode=N`, `i2c:addr=N,stretch=N` (`-d` for short). The flags `--baud`, `--ppm`,
  `--uart-send`, `--mode`, `--i2c-addr` and `--stretch`, and the launch.json keys `baud`,
  `ppm`, `uartSend`, `spiMode`, `i2cAddr` and `stretch`, are gone.
- `piolab asm` without `-o` writes `<out>/<name>.json` (default `build/`).

### Added
- `[tool.piolab]` project config: `pack`, `model`, `cycles`, `out`, `docs` and
  `[tool.piolab.devices]` defaults (`piolab.config`).
- `--model/-m NAME` for `sim` and `debug`, `pytest --model`, and `"model"` in launch.json.
  `piolab models` lists every pack's models.
- Programs by name (`piolab sim uart_rx`), scenarios by name through the pack's new
  `scenarios` module (`--scenario uart_tx`), `--isa` anywhere on the line, and a bare
  `--vcd` / `--listing` writing `<out>/<name>.*`.
- `isa-check` checks the handlers of every model of a pack.
- CAN: a `CanNode` device model (`piolab.devices.can`: CAN 2.0A/B data and remote frames,
  stuffing, CRC-15, arbitration, ACK, error frames and fault confinement up to bus-off, hard
  sync and resync, per-node clock error, injected stuff/CRC/form/bit errors) and an independent
  decoder, `decode_can` (`piolab.decoders.can`). Node↔node loopbacks: `can_link`, `can_bus`
  and `can_arbitration` in `piolab.tests.devices.loopbacks`.
- `CanTransceiver`: the logic side of a CAN transceiver (TXD in, RXD out, the bus open
  drain), with TXD -> bus and bus -> RXD delays and a TXD dominant time-out. `CanNode` gains
  `pins="txd_rxd"` to sit behind one, the way a chip under test meets a CAN bus. Loopback
  `can_transceiver_bus`; tests for the loop delay, arbitration and the time-out.
- `piolab isa-check [--all] [-v]`: checks a pack's yaml against its engine models' `HANDLERS`
  (missing and extra), its directive handlers, semantics text, programs, directed tests and
  generated docs; exits 1 on any gap. `piolab.isa.check.check_pack` is the same set of checks
  for a pack's tests.
- The `new-pack` scaffold writes `models/base/` and a `[tool.piolab]` table, and runs its ISA
  coverage through `check_pack`, with a directed test of `jmp` and `nop`.

### Fixed
- `piolab rtl --scenario` accepts dotted attributes (`mod:Class.factory`), as `sim` does.

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

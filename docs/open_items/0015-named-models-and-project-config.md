# ADR-0015: Named models per pack, project config, a shorter command line

Status: accepted (2026-11-15). Amends ADR-0012 (the pack's `model`).

## Context
- A pack named **one** engine model (`IsaPack.model = "module:callable"`). A project that studies architecture variants of one ISA keeps one model per variant (`<pkg>/models/base/`, `<pkg>/models/dual/`, ...). Anything but the pack's model had to be named in full on every command (`--dut pkg.models.dual:Engine`), and `isa-check` only checked the one model's handlers against the yaml (agreement #6).
- Nothing was configurable per project. Every command repeated the pack (`--isa`, first argument only), the DUT (`sim` ran the **stub** unless given `--dut engine`), the full program path, and a flat set of device flags (`--device uart_partner --baud N --uart-send X --mode N --i2c-addr N --stretch N`).
- The PIO pack was split between `piolab/packs/pio` and `piolab/dut/pio`, so it was not the example of a pack's layout it should be.

## Decision
1. **Models.** `IsaPack.models: {name: "module:callable"}` and `default_model` (default `"base"`). `discover_models("<pkg>.models")` maps each sub-package to `<pkg>.models.<name>:Engine` without importing it, so a new model is a new folder. Each model package exports `Engine` and `HANDLERS`.
   - `--model/-m NAME` on `sim`, `debug`, `pytest`, and `"model"` in launch.json. `--dut` keeps `engine`, `stub`, `tt_template`, `module:callable`, and accepts a model name.
   - `piolab models` lists the packs and their models.
   - `isa-check` checks every model's `HANDLERS` against the yaml.
2. **Project config.** `[tool.piolab]` in the nearest `pyproject.toml` (`piolab.config`): `pack`, `model`, `cycles`, `out`, `docs`, `devices`. Unknown keys are an error. Precedence for the pack: `--isa` > `$PIOLAB_ISA` > `[tool.piolab] pack` > lowest `rank`.
3. **Command line.**
   - `--isa` anywhere on the line.
   - A program is a path or a program name of the active pack (`uart_rx`); a scenario is `module:factory` or a name in the pack's `scenarios` module.
   - `sim` runs the engine by default, like `debug`.
   - Devices are specs: `--device uart:baud=115200,send=0x55,0x41`, `spi:mode=3`, `i2c:addr=0x50,stretch=4` (`piolab.devices.spec`), with defaults from `[tool.piolab.devices]`. The flat device flags are gone.
   - A bare `--vcd` / `--listing` writes `<out>/<name>.*`. `piolab asm` writes to `<out>/` by default.
   - `sim`, `debug` and `dap` build their run from one `RunOptions` (`piolab.run`), so the CLI and launch.json accept the same things.
4. **The PIO pack is self-contained** (`piolab/packs/pio/models/base/`) and has rank 1000: it is the demonstration pack, and an installed project pack is the default.

## Consequences
- Breaking for packs: `model=` becomes `models=` (`discover_models(...)` or a dict). Breaking for scripts and launch configurations: the device flags and `uartSend`/`baud`/`ppm`/`spiMode`/`i2cAddr`/`stretch` keys are replaced by device specs. `piolab asm` without `-o` writes to `build/`, not next to the source.
- `--dut stub` must be given explicitly to run the stand-in from `piolab sim`.
- A model folder that does not export `HANDLERS` is reported as skipped by `isa-check`, not failed.

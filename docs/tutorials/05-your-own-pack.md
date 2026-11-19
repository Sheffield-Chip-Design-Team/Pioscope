# Tutorial 05: your own pack, a second model, and project defaults

**You will:** create an ISA pack in its own project, run its programs by name, add a second engine model by adding a folder, and set the project's defaults so commands need no flags. **Time:** 20 minutes. **Background:** [isa-flow.md §5](../reference/isa-flow.md#5-adding-an-isa-pack), [ADR-0015](../open_items/0015-named-models-and-project-config.md).

This is how a real project uses PIOLab: your ISA lives in your repository, and PIOLab finds it through an entry point. The PIO pack in `piolab/packs/pio/` is laid out the same way, so read it next to your own.

## 1. Scaffold a pack

From the PIOLab checkout:

```bash
uv run piolab new-pack myisa --out ..      # writes ../myisa/
cd ../myisa && uv sync && uv run pytest
```

`new-pack` points the new project at this checkout (`[tool.uv.sources] piolab = { path = ..., editable = true }`), so a change to PIOLab shows up at once. The files that matter here:

| File | What it is |
|---|---|
| `pyproject.toml` | registers the pack (`[project.entry-points."piolab.packs"]`) and sets the project defaults (`[tool.piolab]`) |
| `myisa_isa/pack.py` | `PACK = IsaPack(..., models=discover_models("myisa_isa.models"), ...)` |
| `myisa_isa/isa.yaml` | the ISA: `set`, `jmp`, `nop` |
| `myisa_isa/models/base/` | the `base` engine model: `Core`, `Engine` and `HANDLERS`, one handler per instruction |
| `myisa_isa/programs/blink.myisa` | a program |

## 2. Run programs by name

```bash
uv run piolab models                       # myisa * base default ... (and the built-in pio pack)
uv run piolab sim blink --cycles 40 --vcd  # build/blink.vcd
uv run piolab asm blink --listing          # build/blink.json + build/blink.lst
uv run piolab debug blink -ex "stepi 20" -ex "info cores" --batch
```

- `blink` is a program name: it is looked up in the active pack's `programs/`. A path (`myisa_isa/programs/blink.myisa`) works too.
- The active pack is `myisa` because of `pack = "myisa"` in `[tool.piolab]`. Without that you would need `--isa myisa` (anywhere on the line).
- `sim` and `debug` run the pack's engine model; the `*` in `piolab models` marks the active pack.

## 3. Add a second model

An architecture variant is a new folder in `models/`. Nothing is registered: `discover_models` finds every sub-package that exports `Engine`. Make a `counted` model: the base model plus the `EdgeCounter` unit from [tutorial 04](04-add-a-unit.md).

`myisa_isa/models/counted/__init__.py`:

```python
"""The `counted` model: the base model plus an edge counter on core c0."""

from piolab.examples.edge_counter import EdgeCounter
from piolab.image import ProgramImage

from ..base import HANDLERS
from ..base import Engine as BaseEngine


class Engine(BaseEngine):
    def build(self, image: ProgramImage) -> None:
        super().build(image)
        self.cores[0].units.append(EdgeCounter())


__all__ = ["HANDLERS", "Engine"]
```

Then:

```bash
uv run piolab models          # myisa now has base (default) and counted
uv run piolab isa-check       # "instruction handlers": base: 3 handlers ..., counted: 3 handlers ...
uv run piolab debug blink -m counted --batch -ex "stepi 100" -ex "info units edge_counter"
```

The last command prints `edge_counter: edges=12, level=1`. The trace, the VCD and VS Code show the new unit too, with no tool changes (ADR-0010).

- **Export `HANDLERS`** from every model package: `isa-check` checks each model's handlers against the yaml (agreement #6). A model without it is reported as *skipped*.
- A model that only adds cores or units reuses the base model's handlers, as here. A model with different behaviour has its own `registry()` table.
- `-m NAME` works for `piolab sim`, `piolab debug` and `pytest --model NAME`. The pytest option picks the model for scenario tests that use the `bench` fixture; the scaffold's own tests build `Engine` directly. In VS Code, add `"model": "counted"` to a launch configuration.

## 4. Set the project's defaults

`[tool.piolab]` in `pyproject.toml` is read from any directory of the project:

```toml
[tool.piolab]
pack = "myisa"
model = "counted"       # was "base": the default for sim, debug, VS Code and pytest
cycles = 2000           # default --cycles
out = "build"           # bare --vcd / --listing, `piolab asm` output, the .vsix

[tool.piolab.devices]   # defaults under every --device spec
uart = { baud = 115200 }
```

Change `model` to `"counted"` and run `uv run piolab models`: `default` moves to `counted`. `-m base` still picks the other one. An unknown key is an error that lists the valid ones, so a typo can't be silently ignored.

Precedence: a flag (`--isa`, `-m`, `--cycles`) beats `$PIOLAB_ISA`, which beats `[tool.piolab]`, which beats the pack's own defaults.

## Exercises
1. Add a `dual` model with two cores (`c0`, `c1`) running the same program. What do `info cores` and the VCD show? What happens on the shared `led` pin? Is it a contention error, and why?
2. Give `counted` its own handler table: copy `HANDLERS` and make `nop` count in a unit. Does `isa-check` still pass? What does it report if you delete the `jmp` handler from that table?
3. Write a scenario module, set `scenarios="myisa_isa.scenarios"` in `pack.py`, and run it with `uv run piolab sim --scenario NAME`.

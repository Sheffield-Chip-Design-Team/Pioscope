# Tutorial 06: change an ISA (on a throw-away branch)

**You will:** extend the PIO pack's ISA end to end (spec, assembler, encoder, model, tests and docs), and see every place an ISA reaches. The change is a real one: the RP2350's PIO added `mov pindirs, <src>`, which writes pin *directions* through the out-pin mapping. **Do this on a branch you delete afterwards.** The PIO pack models the RP2040, and a pack only changes to match its ISA more closely, so this change needs an ADR before it could be merged (CLAUDE.md §6 #2). **Reference:** [isa-flow.md](../reference/isa-flow.md).

```bash
git switch -c scratch/isa-tutorial
```

## 1. Spec: `piolab/packs/pio/isa.yaml`

On the RP2350, destination code 3 of `mov` (unused on the RP2040) is `pindirs`. Under `operand_types`, change `mov_dst`:

```yaml
  mov_dst:    {kind: enum, values: [pins, x, y, pindirs, exec, pc, isr, osr], codes: [0, 1, 2, 3, 4, 5, 6, 7]}
```

That is the whole ISA change, because the encoding of an enum value is its `codes:` entry. Run `uv run pytest piolab/packs/pio/tests/test_isa.py`: the generated docs are now stale, so that test fails.

## 2. Assembler and encoder: nothing to write

The assembler is table-driven, and the PIO encoder takes the destination's code from the yaml. Check it:

```bash
printf '.program t\n.pins a=uio0\n.sm_config out=a\nmov pindirs, x\n' > build/t.pio
uv run piolab asm build/t.pio --listing build/t.lst && cat build/t.lst     # a061
```

Add the new form to `FORMS` in `piolab/packs/pio/tests/test_encode.py`:

```python
    ("mov pindirs, x", 0xA061),
```

## 3. Model: `piolab/packs/pio/models/base/exec.py`

`_write(sm, "pindirs", ...)` already exists for `out pindirs`, so `mov pindirs` works as soon as the assembler accepts it. Read `_write` to confirm that it writes the out-pin group's directions. Then add a directed test to `piolab/packs/pio/tests/test_exec.py`:

```python
def test_mov_pindirs() -> None:
    e = engine("set x, 0b101\nmov pindirs, x\nend: jmp end", ".sm_config out=a:3")
    run(e, 2)
    assert e.gpio.oe >> 16 & 0b111 == 0b101
```

An instruction with new behaviour needs a new handler instead: one `@handler("name")` per instruction. Until it exists, `uv run piolab isa-check` reports it under "instruction handlers" ("in the yaml but no handler"), for every model of the pack, and the pack tests fail the same check.

## 4. Generated files

```bash
uv run piolab isa-docs           # docs/pio/isa.md, isa-encoding.adoc/.html
uv run piolab isa-check -v       # yaml vs every model's handlers, programs, tests, docs
uv run piolab vscode             # syntax highlighting
uv run pytest && uv run ruff check piolab && uv run mypy
```

Open `docs/pio/isa-encoding.html`. In the `mov_dst` code table, code 3 is now `pindirs`.

## 5. What a real change also needs
1. A record of the need: what a protocol needed, the workaround, the proposal.
2. An ADR in `docs/open_items/`, agreed before merging (CLAUDE.md §6 #2). For this one: should the pack model the RP2350 too, and how would a program choose?
3. A protocol program that uses it, and its metrics before and after (`piolab metrics`).

## Clean up

```bash
git switch - && git branch -D scratch/isa-tutorial
```

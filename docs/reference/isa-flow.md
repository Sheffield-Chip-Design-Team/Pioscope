# ISA flow reference: from a pack's yaml to a running instruction

**Audience:** engineers who need to read, question or change an ISA, add a new one (an ISA pack), or develop in parallel with others who do. Read [simulator.md](simulator.md) §5 for how instructions execute. The examples use the RP2040 PIO pack (`pioscope/packs/pio/isa.yaml`).

**The rule:** each pack's `<pack>/isa.yaml` is the single source of truth for its ISA (CLAUDE.md §8 #1, ADR-0012). The assembler, the encoder, the docs and the model's set of handlers all derive from it, and tests fail if they drift apart.

```
<pack>/isa.yaml
   │ pioscope/isa/load.py (validate) → pioscope/isa/spec.py (frozen dataclasses: Isa, InstrSpec, ...)
   ▼
pioscope/asm/lexer.py      line → Statement(label, mnemonic|.directive, items, side, delay)
pioscope/asm/operands.py   items typed against operand_types and bound to operand slots
pioscope/asm/directives.py the generic directive handlers, plus the pack's (hooks: directives)
pioscope/asm/assembler.py  pass 1: directives, labels, binding; pass 2: labels, config refs
pioscope/asm/encode.py     IR → words: the generic field encoder, or the pack's (hooks: encoder)
pioscope/asm/listing.py    human-readable listing
   ▼
pioscope/image.py          ProgramImage: IR (authoritative) + words + config + symbols + pack
   ▼
the pack's model      HANDLERS[name](core, IrInstr, first) → completes or stalls
pioscope/scripts/gen_isa_*.py docs/<pack>/isa.md, isa-encoding.adoc, isa-encoding.html
```

---

## 1. The yaml, section by section

| Section | Holds | Read by |
|---|---|---|
| `version`, `name`, `pack` | ISA version (written into every image), its human name, its pack | image, listing, docs, `--dut engine` |
| `hooks` | `directives:` / `encoder:` modules for what yaml cannot say | assembler, encoder, docs |
| `params` | `imem_size`, `word_bits`, `delay_max`, `fifo_depth`, `sysclk_hz`, … | assembler (imem overflow), models, arch study, docs |
| `target` | pads (`ui`/`uo`/`uio`, count, direction), pin modes | `.pins`, board checks |
| `flags` | flag names, if the ISA has any (PIO has none) | conditions, model |
| `builtin_refs` | names a `ref` operand may use without a defining directive | assembler, encoder |
| `operand_types` | named types with a `kind` (below) | operand binder, encoder, docs |
| `conditions` | names for `jmp`/`wait`, optional `arg` type, in encoding order | `cond` operands, model |
| `encoding` | field widths MSB→LSB; `strict`; `roles` and `notes` for the docs | encoder, encoding docs |
| `instructions` | one entry per mnemonic | everything |
| `default_modifiers` | `side`, `delay` | binder |
| `directives` | one entry per `.directive`; `harness: true` for bench-only ones | directive handlers, docs |
| `tiers`, `status` | headings for tiered ISAs; a status line for the reference | docs |

**Operand type kinds:**

| Kind | Matches | Keys |
|---|---|---|
| `int` | an integer literal (dec, `0x`, `0b`, `k`/`M` suffix) or a parenthesised expression | `min`, `max`, `bits`, `bias` (stored value = value − bias) |
| `label` | a program label, or a numeric address | |
| `cond` | a name from `conditions` (+ argument) | |
| `enum` | one of `values` (case-insensitive) | `values`, `codes` (the encoding of each value; default: its index) |
| `flag` | a bare keyword; present = true | `word` |
| `pattern` | a regex over the item text | `regex`, `fields` (named groups, allowed values, first = default), `ints`, `codes` (per field) |
| `ref` | the name of a config object made by a directive | `table` |
| `word` | any identifier | |
| `rest` | all remaining items (directives only) | |

**An instruction entry** (from `pio.yaml`):

```yaml
- name: wait
  tier: 1
  opcode: 1               # the major opcode; unique unless the pack has its own encoder
  operands:
    - {name: pol, type: pol}
    - {name: src, type: wait_src}
    - {name: idx, type: idx5}
    - {name: rel, type: flag, optional: true, default: false}
  semantics: "Stall until the source equals pol: ..."
```

Operand keys:
- `name`: the key in `IrInstr.operands`;
- `type`: an `operand_types` name;
- `optional` / `default`;
- `key`: written as `key=value` in source;
- `field`: the encoding field the operand packs into (default `operand`; generic field encoder only).

**A directive entry** has the same `operands` form, plus `semantics`. Directives never occupy instruction memory. `harness: true` marks configuration the bench needs that the ISA's own assembler doesn't have (PIO's `.pins`, `.sm_config`, `.clkdiv`: what the Pico SDK sets from C).

---

## 2. The pipeline in detail

1. **Load** (`pioscope/isa/load.py`). Parses and validates the YAML: duplicate names, unknown types, widths that overflow their field, regexes that don't compile, unknown hooks. Errors are `IsaSpecError`. `load_isa(path)` is cached per file. `default_isa()` is the active pack's.
2. **Lex** (`pioscope/asm/lexer.py`). One line becomes one `Statement`: `[label:] [mnemonic|.directive] items [side X] [[delay]] ; comment`. Items stay raw text (`x--`, `~osr`, `(N - 1)`), so the lexer knows no instruction.
3. **Bind** (`pioscope/asm/operands.py`). Items are matched to the instruction's operand specs by type and keyword, then converted (ints, expressions after `.define` expansion, enums, patterns). Nothing names an instruction.
4. **Directives** (`pioscope/asm/directives.py`). The `GENERIC` handlers (`.program`, `.pins`, `.side_set`, `.fifo`, `.clkdiv`, `.wrap_target`, `.wrap`, `.define`) plus the pack's `DIRECTIVE_HANDLERS` write `ctx.config`. A pack may also define `resolve_config(ctx, stmts)`, run after pass 1 (PIO resolves `.sm_config` pin names there).
5. **Assemble** (`pioscope/asm/assembler.py`):
   - pass 1: directives, labels, operand binding;
   - pass 2: labels and config references.

   Errors are collected (`AsmErrors`). Each has a span (file, line, column), *expected* and a *hint* (agreement #7).
6. **Encode**: `encoder_for(isa)` is the pack's `hooks: encoder:` module, or the generic field encoder (`pioscope/asm/encode.py`), which packs each operand MSB-first into its field. An encoder provides:
   - `encode_image` and `decode_word`;
   - optionally `delay_max(isa, config)`, the largest `[delay]` (PIO: it shrinks as side-set takes bits);
   - `instr_layout`, `field_usage` and `side_layout`, for the generated encoding documents.

   With `strict: false`, an instruction that doesn't fit gets `None` as its word plus a warning.
7. **Image** (`pioscope/image.py`). `ProgramImage` holds `instructions` (IR, authoritative), `words`, `config`, `symbols`, `source`, `warnings`, `isa_pack` and `word_bits`, and round-trips to JSON.
8. **Execute**: the pack's model has one `@handler("name")` per instruction, and the pack's tests check that the set matches the yaml.
9. **Docs** (`pioscope --isa <pack> isa-docs`). Regenerates `docs/<pack>/isa.md`, `isa-encoding.adoc` and `isa-encoding.html`. Each pack's tests fail when any of them is stale.

---

## 3. Which test guards what (PIO pack)

| If you change… | These tests react | Fix by |
|---|---|---|
| any of `pio.yaml` | `pioscope/packs/pio/tests/test_isa.py` (loads, handlers, stale docs) | `uv run pioscope --isa pio isa-docs` |
| an instruction or its encoding | `test_encode.py` (pioasm output, datasheet forms, round trips) | the encoder, or the spec |
| a mnemonic | `test_isa.py::test_every_instruction_is_used_by_a_program_or_test`, `test_exec.py::test_every_instruction_has_a_handler` | add a directed test and a handler |
| a directive | `test_isa.py::test_every_directive_has_a_handler` | add the handler |
| semantics in the model | `test_exec.py`, `test_scenarios.py` | a directed test per behaviour |

Golden files (listings, error messages), where a pack has them, are rewritten with `uv run pytest --update-golden`. **Read the diff** before you commit them.

---

## 4. Checklists

Before any **semantic** change (what an instruction does, a new instruction, a removed one):
1. Write down the problem (needed / found / workaround / proposal) in the pack's findings log.
2. Write an ADR in `docs/open_items/` with the options and your recommendation.
3. Get agreement from the ISA's owner (CLAUDE.md §8 #2). Don't add an instruction only to make a test pass (#3).

A pack that models an existing ISA (PIO) changes only to match that ISA more closely.

**Add an instruction:**
1. Add a yaml entry with its `opcode`, operands (existing types where possible) and semantics.
2. Add new `operand_types` if needed, preferring an existing kind.
3. Encode it: nothing to do with the generic field encoder; with a pack encoder, add its layout.
4. Add a handler `@handler("name")` in the pack's model, and directed tests.
5. Regenerate the docs (`pioscope --isa <pack> isa-docs`) and the goldens, and review every diff.
6. Add it to a protocol program if one uses it.

**Change operands or a type:** update the YAML, the directed tests and the handler (it reads `ins.operands[...]`), then regenerate docs and goldens.

**Add a directive:**
1. Add a yaml entry.
2. Add a handler writing `ctx.config[...]`, in `GENERIC` if every pack can use it, or in the pack's directives module.
3. Read the new config in the model's config class (`CoreConfig` subclass).
4. Add a test and regenerate the docs.

**Add a condition:** add an entry under `conditions` (its position is its code), a branch in the model's condition function, and a directed test.

---

## 5. Adding an ISA pack

A pack lives in **your own package**, not inside PIOScope, and is registered by an entry point. Start it with the scaffolder:

```
uv add pioscope            # or: git URL, or an editable path for co-development
uv run pioscope new-pack myisa [--pioscope ../pioscope]
cd myisa && uv sync && uv run pytest
uv run pioscope --isa myisa isa-docs
```

`new-pack` writes a project at `./myisa/` with a three-instruction ISA (`set`, `jmp`, `nop`), the generic field encoder, a model, the arch-study data and a `blink.myisa` program:

| File | What it is |
|---|---|
| `pyproject.toml` | depends on `pioscope`; `[project.entry-points."pioscope.packs"] myisa = "myisa_isa.pack:PACK"` |
| `myisa_isa/pack.py` | `PACK = IsaPack(name=..., title=..., spec=HERE/"isa.yaml", programs=HERE/"programs", arch=HERE/"arch", model="myisa_isa.model:Engine", demand="myisa_isa.demand", ext=".myisa")`. `HERE = pack_dir(__file__)` keeps the data package-relative, so it works from a wheel |
| `myisa_isa/isa.yaml` | the ISA: single source of truth (agreement #1). Grow it from here; `pioscope/packs/pio/isa.yaml` shows every feature |
| `myisa_isa/model.py` | `Core` / `Engine` subclasses of `pioscope.dut.core`, one handler per instruction from `registry()`. `pioscope/dut/pio` is a complete example |
| `myisa_isa/demand.py` | arch-study hooks (`units_used`, `bit_shares`, `fifos`, `bit_rate`) |
| `myisa_isa/arch/*.yaml` | area costs, architectures, workloads. Edit the workloads to name your programs |
| `tests/` | a test that every yaml instruction has a handler and the blink program runs; `conftest.py` activates the pack with `pioscope.packs.use` |

Generated documents go to `docs/<name>` under the directory you run the tools from. `rank` on the `IsaPack` decides the default pack when several are installed and no `--isa` / `$PIOSCOPE_ISA` is given.

After the skeleton works:
- **Hooks**, only if the yaml can't say it: directive handlers (`registry()` from `pioscope.asm.directives`) and an encoder module (§2 step 6), named under `hooks:` in the yaml. See `pioscope/packs/pio/directives.py` and `encode.py`.
- Run `uv run pioscope vscode`: the editor grammar picks up the new mnemonics and the new file suffix.

---

## 6. Developing in parallel

An ISA is shared by everyone who writes programs for it, so changes to it must be small and visible.

- **Propose ISA changes on their own branch** (`isa/<topic>`), with:
  - the finding and the ADR;
  - the YAML change;
  - the handler and the tests;
  - the regenerated docs and goldens.

  Keep each one small, and merge it before the protocol work that uses it.
- **YAML merge conflicts:**
  1. Resolve the YAML by hand, keeping both entries.
  2. Run `pioscope isa-docs` and `pytest --update-golden`.
  3. Never hand-merge generated files (`docs/<pack>/isa*`, golden `.lst`); regenerate them.
- **Numbers that must be unique:** opcodes (where the encoder needs it), finding numbers and `ADR-NNNN` numbers. Claim the next number in your first commit, so clashes show up early.
- **Experiments without changing the ISA:** `pioscope.isa.load_isa(path)` loads any YAML, and `pioscope.asm.assemble(source, file, isa=...)` assembles against it. Copy a pack's yaml to a scratch file to try an idea before proposing it.


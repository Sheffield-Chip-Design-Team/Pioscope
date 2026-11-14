# Debugger reference: how `pioscope debug` and the VS Code adapter work

**Audience:** engineers who want to change the debugger itself: add a command, show something new, or support a new kind of model. To *use* the debugger, read [debugging.md](../debugging.md). Usually you won't need this page: a model that follows the observability contract ([simulator.md](simulator.md) §7) is shown without any debugger change.

```
                ┌───────────── sim/debug/session.py: DebugSession ─────────────┐
 pioscope debug ──┤ owns a PySession (the same cycle loop as pioscope sim) + a DUT  │
  repl.py       │ breakpoints, watches, catches, selected core, eval, step  │
                │ write_vcd/to_gtkw at every stop                                │
 pioscope dap ────┤                                                              │
  dap.py        └──────────────────────────────────────────────────────────────┘
  (VS Code: tools/vscode-pioscope/extension.js starts `uv run pioscope dap`)
```

## 1. `DebugSession` (session.py)

| Part | What it does |
|---|---|
| `__init__(factory, dut="engine")` | `factory()` builds the `Scenario`; `dut` is a factory or a `--dut` name (`engine`: the program's ISA pack's model). `restart()` makes a fresh `PySession(scenario, dut)`. |
| `cores`, `names`, `selected`, `select()` | the model's cores; `step`/`finish`/`print` act on the selected one |
| `resolve(where, ti)` | label, `*addr`, `line`, `file:line`, optionally `<core>:` → an address in a core's program |
| `Breakpoint.addrs` | {core index: address}; a breakpoint can apply to several cores |
| `_at_breakpoint()` | before each cycle, for every core whose `issue_pc()` is not None, compare with the breakpoints. A hit selects that core. |
| `_run(until, max_cycles)` | the one loop: stops on finish, `until`, breakpoint, cycle limit, pause, catch, watch or `EngineError` |
| `stepi` / `step` / `finish` / `cont` / `until` | stopping conditions built on `_run` (`step`: the selected core's `issued` count went up and its next instruction is about to issue) |
| `eval(path, ti)` | `$cycle`, `$time`, a path from the top, from a named core, from the selected core, or a unique bare name (§3 of the guide) |
| `set_var(path, value)` | calls `core.set_reg()` (x, y, osr, isr, pc) |
| `_stop(stop)` | records the stop, tags it with the core, and rewrites the VCD and `.gtkw` if `vcd_path` is set |
| `where(ti)`, `disassemble(start, n, ti)`, `line_of(addr, ti)` | presentation helpers used by both front ends |

The session never reads a register by name except through `state()` and `set_reg()`, which is what keeps it model-agnostic.

## 2. The terminal front end (repl.py)

`Repl` is a `cmd.Cmd`. Each `do_<name>(arg)` method is a command, and its docstring is its `help` text. Aliases are assignments (`do_c = do_continue`). Errors raised as `DebugError`, `ValueError` or `KeyError` are printed as `error: …` and never end the session.

**Add a command:**
1. Add a method `do_mycmd(self, arg: str) -> None` with a one-paragraph docstring.
2. Use `self.s` (the session) and `self.out(text)` to print.
3. If it runs the simulation, call `self.report(stop)` afterwards.
4. Add a case to `sim/packs/pio/tests/test_debug.py` using the `kdb(...)` helper, which runs commands and returns the output.
5. Add a row to the command table in [debugging.md](../debugging.md).

The Debug Console in VS Code runs the same `Repl` for any line starting with a known command name. Running and stepping commands are refused there (use the toolbar).

## 3. The VS Code adapter (dap.py)

`DapServer` reads Debug Adapter Protocol requests (JSON with a `Content-Length` header) from stdin and calls `req_<command>`. Running requests execute in a worker thread so that `pause` is heard.

| DAP request | Implementation |
|---|---|
| `launch` | `factory_from_launch(args)` (the same scenario building as `pioscope sim`) and `dut_from_launch(args)`; optional `vcd` |
| `threads` | one per core, id = index + 1 |
| `stackTrace` | one frame per thread, frame id = thread id; `instructionPointerReference` = `0xTAAA` |
| `scopes` | "Core <name>" (the scalars of its state), one scope per dict-valued key (titles from `TITLES`, else title-cased), "Shared" if any, "Timing" |
| `variables` | walks the nested dicts and lists; every leaf gets an `evaluateName` (a full path such as `sm0.regs.isr`) |
| `setVariable`, `dataBreakpointInfo`, `setDataBreakpoints` | `set_var`, `eval`, `add_watch` with full paths |
| `next` / `stepIn` / `stepOut` / `continue` | select the thread's core, then `step` / `stepi` / `finish` / `cont` |
| `setBreakpoints` / `setInstructionBreakpoints` | `set_line_breakpoints` / `set_instruction_breakpoints` |
| `disassemble` | `disassemble(start, n, ti)`; addresses `0xTAAA` |
| `evaluate` | a kdb command (context `repl`), or `eval(expr, ti)` |

**Add a launch attribute:**
1. Read it in `factory_from_launch`, `dut_from_launch` or `req_launch`.
2. Declare it in `tools/vscode-pioscope/package.json` under `configurationAttributes.launch.properties`. `test_extension_launch_attributes_match_the_adapter` checks that the two agree.
3. Rebuild the extension: `uv run python tools/vscode-pioscope/build.py`, then `code --install-extension tools/vscode-pioscope/pioscope-asm-0.1.0.vsix`, then reload the window.

**Debugging the adapter.** The `Dap` class in `sim/packs/pio/tests/test_debug.py` is a small DAP client; copy a test and print the messages. An adapter exception is sent to the Debug Console as stderr, and the request fails with its message.

## 4. The extension (tools/vscode-pioscope)

| File | Contents |
|---|---|
| `package.json` | the language, grammar, debugger type `pioscope`, launch attributes, `pioscope.adapterCommand` setting |
| `extension.js` | default configuration for F5 without `launch.json`; finds the project directory; starts `uv run pioscope dap` |
| `build.py` | regenerates `pioscope.tmLanguage.json` from every ISA pack's yaml (mnemonics, directives, conditions, keywords), sets the language's file suffixes, and packs the `.vsix` |

The grammar is generated, so new instructions are highlighted after a rebuild (`test_extension_grammar_is_current` fails until you do).

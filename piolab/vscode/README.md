# PIOLab assembly for VS Code

Syntax highlighting for the ISA packs' assembly (`.pio` for RP2040 PIO, and every other pack's suffix) and a step debugger that runs your program on its pack's engine model, with bench devices attached.

- Set breakpoints on source lines and press F5.
- **Step Over** runs one instruction; a multi-cycle instruction counts as one.
- **Step Into** runs one sysclk cycle.
- **Step Out** finishes the current instruction.
- The Variables view shows the registers, flags, FIFOs, units, pins and any group the model adds (Config on PIO), plus the shared units. Right-click a variable and choose "Break on Value Change" to set a watch.
- The Debug Console takes debugger commands, e.g. `info shared`, `p/x isr`, `x/8i`, `catch event partner:rx`, `info events 20`.

Install (from the project directory, in the VS Code terminal):

```bash
uv run piolab vscode
code --install-extension build/piolab-asm-0.1.0.vsix
```

Then run "Developer: Reload Window". See `docs/debugging.md` for the full guide.

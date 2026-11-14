# Documentation map

**New here?** Read in this order:
1. [README.md](../README.md): what this is and how to run it. [CLAUDE.md](../CLAUDE.md) has the working rules.
2. [debugging.md](debugging.md) §0: set up. Then do [tutorials 01–04](tutorials/).
3. [pio/machine-model.md](pio/machine-model.md): the example ISA pack's model, the RP2040 PIO.

| I want to … | Read |
|---|---|
| run, trace, view waveforms, step in VS Code | [debugging.md](debugging.md) |
| learn by doing | [tutorials/](tutorials/): 01 first program · 02 device + decoder · 03 add a probe · 04 add a unit · 06 change an ISA |
| understand the simulator code | [reference/simulator.md](reference/simulator.md) |
| add or change an ISA (an ISA pack) | [reference/isa-flow.md](reference/isa-flow.md), [ADR-0012](open_items/0012-isa-packs.md) |
| read the PIO pack's ISA | [pio/isa.md](pio/isa.md) and [pio/isa-encoding.html](pio/isa-encoding.html) (generated) |
| change the debugger | [reference/debugger.md](reference/debugger.md) |
| write a device, decoder or scenario | [bench.md](bench.md), [tutorial 02](tutorials/02-device-and-decoder.md) |
| specify a unit | [requirements/units/](requirements/units/TEMPLATE.md) |
| compare architectures | [arch-guide.md](arch-guide.md), [pio/arch-study.md](pio/arch-study.md), [pio/arch-study.html](pio/arch-study.html) |
| see why something is the way it is | [open_items/](open_items/): the ADRs (a number missing from the sequence is a decision kept outside this repository) |

Each ISA pack has its own directory of generated documents (`docs/<pack>/`), written by `pioscope --isa <pack> isa-docs` and `pioscope --isa <pack> arch --write`.


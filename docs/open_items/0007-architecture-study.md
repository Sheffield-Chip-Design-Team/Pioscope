# ADR-0007: Architecture study — fixed PIO-style cores vs a software-allocated mesh

Status: accepted (2026-11-10)

For the vocabulary, formulas and worked examples, see [arch-guide.md](../arch-guide.md).

## Context
The baseline organisation is PIO-style cores (the RP2040's state machines), each with its own instruction memory (imem), FIFOs and units. An alternative is a mesh: pools of cores, imem banks, units and FIFOs joined by an interconnect, with software deciding at load time which protocol gets what. A third option is a runtime meta-controller that moves resources between protocols as they run.

These choices decide the engine's area and how much of it sits idle. Comparing them through full engine models would wait on those models.

## Decision
- **Analytic model first, cycle-accurate confirmation later.** `piolab/arch/` sizes and scores architectures from static program analysis. `uv run piolab --isa <pack> arch --write` regenerates `docs/<pack>/arch-study.md` (ADR-0012), and a test fails when the committed copy is stale.
- **Static allocation only.** A runtime meta-controller is time-sharing, which is out of scope for this analytic model (ADR-0011 brings it into the cycle-accurate models). It stays an extension point. A mesh allocation can later be re-run at a frame boundary without changing this model.
- **Metrics: area and utilisation.**
  - Area is in gate equivalents (GE; 1 GE = `sg13g2_nand2_1`, 7.2576 µm²).
  - Flip-flop, mux and SRAM-macro areas come from the IHP SG13G2 PDK (the stdcell LEF and the SRAM datasheets; see the pack's `<pack>/arch/cost.yaml`). The block costs (core, units, FIFO control, link width) are placeholders, so the report re-ranks the architectures with each one scaled ×0.5 and ×2.
  - Utilisation is reported as allocated share (resources assigned to some program) and active share (estimated busy fraction).
- **Inputs.**
  - Demand comes from the assembled programs (`piolab/arch/demand.py` with the pack's `demand` hooks): imem words, units, FIFOs, pads, and the share of bit-times each unit is busy.
  - Protocol mixes with traffic loads are in the pack's `workloads.yaml`; the chip must support each mix, one at a time.
  - Hand-picked architectures are in the pack's `architectures.yaml`.
- **Families compared** (smallest configuration that fits every mix):
  - PIO with every unit on every core, or only the unit types some program uses.
  - PIO with one shared imem (RP2040 style).
  - Mesh with a full crossbar.
  - Mesh in clusters of 2 cores.
- **Allocator.** The mesh allocator is the "higher-level software". It is exact (backtracking over clusters); a first-fit variant shows what a cheap allocator loses.

## Model assumptions (revisit with synthesis or the engine model)
- **Storage:** the project targets IHP SG13CMOS5L (`.github/workflows/gds.yaml`), which has SRAM macros (TinyTapeout `ttihp-cmos5l-sram-example`; catalogue in the IHP-Open-PDK docs).
  - imem is either flops or the cheapest set of 64- or 256-word macros (1P or 2P) covering the words, the word width and the read ports. The sweep tries both and keeps the cheaper.
  - FIFOs are always flops.
  - An N:1 read or select mux costs N−1 `sg13g2_mux2_1` per bit.
  - Which macros SG13CMOS5L offers is not stated in the PDK docs. The catalogue is the SG13G2 list, and the TT example uses `RM_IHPSG13_1P_256x16_c2_bm_bist` on SG13CMOS5L.
- **Provisioned imem:** the headline comparison provisions at least 32 words per core, so every family has the same program capacity. The report also sweeps 0, 16, 32 and 64 words.
- **Mesh read ports:** each core reads any word in its cluster's banks, so its read mux covers the whole cluster. This is the main cost of pooling imem.
- **Mesh links:** each core↔unit link is `unit_port_bits` wide. Each core picks its FIFOs from the cluster's pool, over word-wide paths.
- **Activity is estimated:** a core is busy while its line is busy (the traffic `load`). A unit is busy for its share of those bit-times. Units configured for the whole program count as always busy.
- **Pads are remappable:** only the total is checked against the 24 tile pads. The programs' hard-coded pads are ignored.
- **Not yet modelled:**
  - Contention or latency: allocation is exclusive.
  - Clock dividers per core vs per pool.
  - Host-port wiring, which is the same for all families.

## Consequences
- **PIO pack** (`docs/pio/arch-study.md`): with no units, the comparison is cores, imem and FIFOs. An RP2040-style block (4 SMs, 32 shared words) fits every mix except `io_hub`, whose four programs need 37 words.
- **SRAM affects the engine design, not only its area.** Macro reads are synchronous (registered address, one-cycle access). PIO executes a `jmp` in one cycle with no penalty, so an SRAM imem needs fetch-ahead or a cycle of branch delay. The engine model must decide which, in its own ADR. A 64-word macro is also wider than one tile (236.8 µm vs about 202 µm), so an SRAM imem sets a minimum tile shape. The TT example's SRAM power scheme is not yet proven in silicon.
- **Phase 2:** build engine models from the reusable parts (`piolab.dut.core`: `Core`, `Fifo`, `Unit`) and a `Fabric(arch, allocation)` that implements `Dut` for either family. This needs:
  - `Dut.load(image, tc)` and `Scenario.images` (an ADR of their own).
  - Relocating `jmp` targets and `.wrap` for banked imem.
  - Per-unit activity counters in `probes()`, so the report can show estimated vs measured utilisation.
- **The ranking depends on the workload set**, so update the mixes as programs are written.

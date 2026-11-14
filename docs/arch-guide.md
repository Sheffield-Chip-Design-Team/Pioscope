# Guide to the architecture study: concepts, formulas and metrics

**Intended audience:** hardware engineers who want to read, question or extend the architecture study.

**What this guide is not:** a results summary. Each ISA pack's results are in `docs/<pack>/arch-study.md` and, interactively, in `docs/<pack>/arch-study.html`; for the example pack, [pio/arch-study.md](pio/arch-study.md) and [pio/arch-study.html](pio/arch-study.html). The results change whenever the inputs do. This guide explains the vocabulary and the arithmetic behind them. The worked examples use the PIO pack's inputs (`pioscope/packs/pio/arch/`) as of 2026-11-14.

Method, scope and decisions: [ADR-0007](open_items/0007-architecture-study.md). The code is in `pioscope/arch/`.

---

## 1. The question

A programmable IO engine in the style of the RP2040's PIO runs small protocol programs on small cores. An ISA may also attach hardware **units** that do expensive per-bit work (CRC, bit stuffing, line coding, timing), so the programs stay short. The RP2040 PIO itself has none.

The study asks how that hardware should be organised:
- **PIO-style:** a fixed set of identical cores, each with its own instruction memory and its own copy of every unit.
- **Mesh:** pools of cores, memory banks, units and FIFOs. Software decides at load time which protocol gets which pieces.

Both are judged on **area** (silicon cost) and **utilisation** (how much of that silicon does useful work) over a set of protocol mixes (workloads). The inputs are per ISA pack: its programs, its `workloads.yaml`, its `architectures.yaml` and its `cost.yaml`.

Not modelled *in this analytic study*:
- **runtime reallocation**, where a controller moves resources between protocols as they run;
- **contention** for shared resources.

Both are in scope for the cycle-accurate models (ADR-0011), and their measured results will feed back into this study.

---

## 2. Building blocks

### 2.1 The core

The core is the "CPU" of one protocol. It contains:
- a program counter;
- scratch registers X and Y;
- an output shift register (OSR) and an input shift register (ISR), with shift counters;
- the instruction decoder;
- a clock divider;
- any flags its ISA defines.

A core runs **one program** at a time. The study prices it as one lump, `core` in `cost.yaml` (a placeholder).

### 2.2 Instruction memory (imem)

imem holds a program's instructions.
- **Word:** one instruction, `word_bits` in the pack's ISA yaml (16 bits on PIO).
- **Size:** today's PIO programs are 2–22 words (`pioscope --isa pio metrics`). The RP2040 has **32 words** shared by 4 state machines.
- **Storage technology:** imem is built one of two ways, and the study picks whichever is cheaper for each configuration:
  - **flip-flops:** one `sg13g2_dfrbp_1` per bit, plus a **read mux** that selects the addressed word;
  - **SRAM macros:** blocks supplied by the IHP PDK. The smallest is 64 words × 16 bits, so a 16-bit word takes one and a 32-bit word a pair. A macro contains its own address decoder, so it needs no read mux.
- **Read port:** one independent read per cycle. A core fetches one instruction per cycle, so every core reading a memory needs its own port. IHP macros have **at most 2 ports**.
- **Read latency:** SRAM reads are **synchronous**: the address is registered and the data arrives a cycle later. That affects branch timing in the engine (ADR-0007, Consequences), but not area.

### 2.3 FIFOs

FIFOs connect the host (the CPU side) to a core:
- **TX FIFO:** host → core, for words to send;
- **RX FIFO:** core → host, for words received.

Depth is `fifo_depth` in the ISA yaml (4 on PIO). Width is `fifo_bits` (default `word_bits`; 32 on PIO). FIFOs are always flip-flops. Which FIFOs a program needs comes from its pack's `fifos` hook. On PIO, a program needs a TX FIFO for `pull`, or `out` with autopull, and an RX FIFO for `push`, or `in` with autopush.

### 2.4 Units

A pack lists its unit types, with their prices, under `units:` in its `cost.yaml`. Each unit's demand comes from the pack's `demand` hooks:
- `units_used`: the units a program needs;
- `bit_shares`: for each unit, how much of a frame's bit-times it is busy.

The RP2040 PIO has no units (`units: {}`), so its study compares cores, imem and FIFOs only. A pack with units adds:
- `unit:<type>` area classes;
- unit pools in the mesh;
- crossbar links to them.

Everything below applies to both cases.

---

## 3. Architecture families

The study compares five **families**. Each is sized to the smallest configuration that fits every workload (§7). The code is in `pioscope/arch/arch.py` and `minimal()` in `pioscope/arch/evaluate.py`; the one-line descriptions are `FAMILY_TEXT`.

### 3.1 PIO-style: `pio_full` and `pio_lean`

```
 t0                  t1                  t2                  t3
┌──────────┐        ┌──────────┐        ┌──────────┐        ┌──────────┐
│ core     │        │ core     │        │ core     │        │ core     │
│ imem 32w │        │ imem 32w │        │ imem 32w │        │ imem 32w │
│ units    │        │ units    │        │ units    │        │ units    │
│ TX  RX   │        │ TX  RX   │        │ TX  RX   │        │ TX  RX   │
└──────────┘        └──────────┘        └──────────┘        └──────────┘
```

- **What it is:** N identical cores. Each owns its imem, its TX/RX FIFOs and one instance of every unit in its unit set.
- **`pio_full`:** every core carries every unit type.
- **`pio_lean`:** every core carries only the unit types that *some* program in the workloads uses. With no units (PIO) the two are the same.
- **Strength:** no switching logic and no allocation problem. Any program runs on any core.
- **Weakness:** a unit that one program in four needs is still built four times.

### 3.2 PIO with shared imem: `pio_shared`

- **What it is:** like `pio_lean`, but all cores fetch from one memory, as on the RP2040 (32 words shared by 4 SMs).
- **Strength:** short programs pack together, so less memory is wasted.
- **Weakness:** every core fetches every cycle, so the memory needs **one read port per core**.
  - With flip-flops, that means N wide read muxes.
  - With SRAM it is impossible beyond 2 cores, because IHP macros have at most 2 ports.

### 3.3 Mesh with a full crossbar: `mesh_full`

```
 cores:   [c0] [c1] [c2] [c3]
             ╲   │   │   ╱
          ═════ crossbar ═════        any core ↔ any bank, unit, FIFO
             ╱   │   │   ╲
 banks:   [b0] [b1] [b2] [b3] …
 units:   (the pack's unit pools, if any)
 FIFOs:   [f0] [f1] … [f5]
```

- **What it is:** resources are **pooled**. Software assigns each program a core, enough imem **banks** to hold it, the units it needs, and its FIFOs.
- **Pool sizes:** sized to the most any workload needs at once, not one per core. That is where the saving comes from.
- **Bank:** a fixed-size block of imem, either 2–16 words of flip-flops or one SRAM macro set.
- **Crossbar:** the switching that connects any core to any pooled resource, built from muxes in both directions.
- **Allocation-select config:** flip-flops, written once at load time, that hold each core's choices (which unit instance, which FIFOs, which banks).
- **Weakness:** crossbar and config area. With flip-flop imem, each core's read mux also spans every word in the pool, which makes the full crossbar expensive.

### 3.4 Clustered mesh: `mesh_c2`

```
 ┌ cluster 0 ───────────────┐   ┌ cluster 1 ───────────────┐
 │ [c0] [c1]                │   │ [c2] [c3]                │
 │ ═══ cluster crossbar ═══ │   │ ═══ cluster crossbar ═══ │
 │ banks, units, FIFOs (½)  │   │ banks, units, FIFOs (½)  │
 └──────────────────────────┘   └──────────────────────────┘
```

- **What it is:** cores are grouped into **clusters** of 2. Each cluster gets an equal share of every pool and a crossbar only inside the cluster.
- **Trade-off:** the muxes shrink, but allocation gets harder. A program can only use its own cluster's resources.

---

## 4. Allocation: the "higher-level software"

Allocation happens **at load time**, before the programs run, and stays fixed while they run. Each program needs one core. The code is in `pioscope/arch/allocate.py`.

- **PIO** (`allocate_pio`): program *i* goes on core *i*. It fails if there are more programs than cores, or if a program needs a unit, more imem words or a deeper FIFO than a core has. With shared imem, it also fails when the programs together need more words than the memory has.
- **Mesh** (`allocate_mesh`): each program's demand must fit inside one cluster's remaining capacity: 1 core, ⌈words / bank_words⌉ banks, its FIFOs, and one of each unit it uses. This is a small **bin-packing** problem. There are two allocators:
  - **exact:** backtracking over clusters, biggest programs first. It finds an assignment if one exists. The study uses this one.
  - **greedy:** first fit, in workload order. It is cheap, but it can fail where exact succeeds.

**Worked example** (PIO programs): 4 cores in 2 clusters and 8 banks of 4 words, so each cluster has 2 cores and 4 banks.

| Program | Words | Banks |
|---|---|---|
| `spi_m0` | 2 | 1 |
| `spi_m1` | 3 | 1 |
| `uart_rx` | 9 | 3 |
| `uart_rx` | 9 | 3 |

- **Greedy** puts both SPI programs in cluster 0 (2 banks; both cores used). It then needs 3 + 3 banks in cluster 1, which has 4, so it fails.
- **Exact** pairs each big program with a small one: {`uart_rx`, `spi_m0`} = 4 banks and {`uart_rx`, `spi_m1`} = 4 banks. It fits.

**Infeasible** means no assignment exists. The report and page give the reason. For example, "programs total 37 words; shared imem is 32" is why the PIO pack's `io_hub` mix does not fit one RP2040 block.

---

## 5. Units of measure

| Unit | Meaning | Source |
|---|---|---|
| **GE** (gate equivalent) | Area in units of one 2-input NAND, `sg13g2_nand2_1`: **1 GE = 7.2576 µm²**. It lets logic, flops and macros be added up. | PDK stdcell LEF (sourced) |
| flop bit | One `sg13g2_dfrbp_1` flip-flop = 52.6176 µm² = **7.25 GE** | PDK LEF (sourced) |
| mux bit | One bit of a 2:1 mux, `sg13g2_mux2_1` = 18.144 µm² = **2.5 GE**. An N:1 mux costs N−1 of them per bit. | PDK LEF (sourced) |
| **µm²** | Physical area. SRAM macros are given in µm² and converted to GE by ÷ 7.2576. | IHP SRAM datasheets (sourced) |
| **tile** | One Tiny Tapeout tile, about 202 × 155 µm = 31,310 µm² ≈ **4,314 GE**. The report counts tiles **at 100% density**; real placement and routing need noticeably more. A 64-word macro (236.8 µm wide) is wider than one tile. | Tiny Tapeout tile size |
| **word** | One instruction (`word_bits`) or one FIFO entry (`fifo_bits`) | the pack's ISA yaml |
| **load** | The fraction of time a protocol's line is busy (0–1). It is an input, set per program in each workload. | `workloads.yaml` (assumed) |
| **bit share** | For one pass of a program, the share of bit-times during which a unit is busy. | the pack's `bit_shares` hook (static estimate) |
| **activity** | The estimated fraction of cycles a resource does useful work. Core: activity = load. Unit: activity = load × bit share. | `ResourceDemand.activity` (estimate) |

Two kinds of number appear throughout:
- **sourced:** cell and macro areas from the PDK;
- **placeholder:** the block costs (`core` 1,800, `fifo_control` 80, `unit_port_bits` 8, `config_bit` 6, and any unit costs). Synthesis will replace these.

---

## 6. The area model

The code is `area()` in `pioscope/arch/cost.py`. Notation:
- W = words;
- b = bits per instruction word (`word_bits`), d = bits per FIFO entry (`fifo_bits`);
- f = flop bit (7.25 GE), m = mux bit (2.5 GE);
- n = cores or cores, k = clusters, cc = cores per cluster.

| Class | PIO (n cores, W words each) | Mesh |
|---|---|---|
| `core` | n × core | cores × core |
| `imem` (flops) | n·W·b·f (shared imem: W·b·f) | banks × bank_words × b·f |
| `imem` (SRAM) | n × cheapest macro set for W words × 1 port (shared imem: one set with n ports) | banks × cheapest macro set for bank_words × 1 port |
| `imem_read` | flops: n × (W−1)·b·m; SRAM: 0 | flops: cores × (words in cluster − 1)·b·m; SRAM: cores × (banks in cluster − 1)·b·m |
| `fifo` | 2n × (depth·d·f + fifo_control) | fifos × (depth·d·f + fifo_control) |
| `unit:<type>` | n × unit cost, for each type in the set | pool count × unit cost |
| `xbar` | 0 (hard-wired) | per cluster: units (per type with n_u in the cluster: (cc·(n_u−1) + n_u·(cc−1)) × unit_port_bits × m) plus FIFOs the same with d-bit paths |
| `config` | 0 | (Σ_types ⌈log₂ n_u⌉ + 2⌈log₂ fifos_c⌉ + ⌈log₂ banks_c⌉) × cores × config_bit |

**Overhead** = `imem_read` + `xbar` + `config`: area that never does protocol work.

**SRAM macro choice** (`Cost.sram_imem`): for each catalogued macro with enough ports, the model uses ⌈W / macro words⌉ × ⌈b / macro bits⌉ macros, and keeps the cheapest arrangement. The catalogue is in `cost.yaml`: the 64- and 256-word IHP macros, 1-port and 2-port.

**Worked example: when does SRAM beat flip-flops?** For PIO's 16-bit word, one word of flip-flops costs 16 × 7.25 = **116 GE** of storage, plus 16 × 2.5 = **40 GE** of read mux for each word beyond the first.

| Words | Flip-flops | SRAM (1 × `RM_IHPSG13_1P_64x16_c2`) |
|---|---|---|
| 12 | 1,832 GE | 2,100 GE |
| 13 | 1,988 GE | 2,100 GE |
| 14 | **2,144 GE** | **2,100 GE** ← SRAM now cheaper |
| 32 | 4,952 GE | 2,100 GE |
| 64 | 9,944 GE | 2,100 GE |

The SRAM figure is 15,240.45 µm² ÷ 7.2576 = 2,100 GE, for anything up to 64 words. Above about 14 words SRAM wins, and up to 64 words its cost doesn't change. (For a 32-bit word both columns double, and the crossover stays at 14 words.)

---

## 7. Sizing and provisioning

**The smallest configuration that fits every workload:** for each family, the study finds the cheapest configuration on which *every* workload can be allocated, one at a time (`minimal()` in `evaluate.py`). The chip is configured for one mix at load time, but it must be able to serve any of them.
- **PIO:** cores = the largest number of programs in any mix; imem = the largest program; units = all types (full) or the types used (lean).
- **Full mesh:** each pool = the most any one mix needs at once.
- **Clustered mesh:** a search over per-cluster pool sizes, cheapest first, until one fits every mix with the exact allocator.

**Headroom (provisioned imem):** a real chip needs room for programs that haven't been written yet. The headline comparison provisions **at least 32 words per core**, so every family offers the same program capacity. The report and page also sweep **0, 16, 32 and 64** words per core; 0 means just enough for today's programs.

The smallest SRAM macro set already holds 64 words, so headroom up to 64 words costs SRAM designs nothing, while flip-flop designs grow linearly. That is why the provisioning chart's lines go flat once they switch to SRAM.

---

## 8. The metrics

Every (architecture, workload) pair gets a **score** (`Score` in `evaluate.py`). All of the metrics below are shares from 0 to 1.

**Allocated share per resource class:** how much of each class is assigned to some program.
- cores: programs ÷ cores;
- imem: words used ÷ words provided;
- FIFOs: FIFOs used ÷ FIFOs provided;
- units: unit instances used ÷ unit instances provided.

**Active share** (estimated): how busy the logic is.
- cores: Σ load ÷ cores;
- units: Σ (load × bit share) ÷ unit instances.

**Area fractions:** the headline utilisation numbers, as shares of the architecture's total area.

| Metric | Definition |
|---|---|
| **allocated area** | the sum over programs of its core, the units it uses, its imem words × (this architecture's imem GE ÷ its words), and its FIFOs × FIFO GE, divided by the total |
| **active area** | the same sum, but core and units weighted by their activity. Storage counts in full while it holds the program. |
| **overhead** | (imem_read + xbar + config) ÷ total |

Low allocated area means hardware was built that this mix doesn't use. Low active area means hardware is assigned but mostly idle. Overhead is the area the flexibility costs.

**Worked example: PIO `io_hub` on `mesh_c2`** (32 words/core).
- **The architecture:** 4 cores in 2 clusters, 4 SRAM banks of 64 words, and 6 FIFOs. Its total area is 23,048 GE. Its imem is 8,400 GE for 256 words, which is **32.8 GE per word**. One FIFO is 4 × 32 × 7.25 + 80 = **1,008 GE**.
- **The workload:** four programs.

| Program | Load | Words | FIFOs | Storage (words × 32.8 + FIFOs × 1,008) | Allocated | Active |
|---|---|---|---|---|---|---|
| `uart_tx` | 0.3 | 4 | 1 | 1,139 | 1,800 + 1,139 = 2,939 | 0.3·1,800 + 1,139 = 1,679 |
| `uart_rx` | 0.3 | 9 | 1 | 1,303 | 1,800 + 1,303 = 3,103 | 0.3·1,800 + 1,303 = 1,843 |
| `spi_m0` | 0.8 | 2 | 2 | 2,082 | 1,800 + 2,082 = 3,882 | 0.8·1,800 + 2,082 = 3,522 |
| `i2c_write` | 0.1 | 22 | 2 | 2,738 | 1,800 + 2,738 = 4,538 | 0.1·1,800 + 2,738 = 2,918 |
| **sum** | | | | | **14,462** | **9,962** |

- **Allocated area:** 14,462 ÷ 23,048 = **62.7%**.
- **Active area:** 9,962 ÷ 23,048 = **43.2%**.
- **Shares:** cores 4/4 = 100% allocated; imem 37/256 = 14% allocated; FIFOs 6/6 = 100%.

These match the study's `score()`.

---

## 9. Sensitivity

The placeholder costs are guesses, so the report re-ranks the families with **each constant on its own** halved (×0.5) and doubled (×2) (`sensitivity()` in `evaluate.py`).
- **Swept:** `flop_bit`, `mux2_bit`, `core`, `fifo_control`, `unit_port_bits`, `config_bit`, and each unit cost.
- **Held fixed:** the GE conversion (`nand2_um2`) and the SRAM macro areas, which are taken from the PDK.

How to read the effect column:
- **stable:** the order of all five families is unchanged.
- **order changes:** some families swap places, but the smallest one stays the same.
- **leader changes:** the smallest family is different. A conclusion that depends on this constant shouldn't be trusted until synthesis pins it down.

The study varies one constant at a time, so two wrong constants together can still change the ranking.

---

## 10. Reading the outputs

`uv run pioscope --isa <pack> arch --write` regenerates both files in `docs/<pack>/`.

**arch-study.html**, panel by panel:
1. **Architecture and allocation:** pick an architecture and a workload. Each block is one piece of hardware.
   - **Colour:** the program using the block (legend above the diagram).
   - **Bar along the bottom:** how busy it is (activity) or, for storage, how full.
   - **Hatched:** idle in this workload.
   - **Hover or tab to a block** for its program, load, activity and GE. If the workload doesn't fit, a banner gives the allocator's reason.
2. **Utilisation heatmap:** every architecture × workload. Switch between active and allocated area. A stronger blue means more utilised (darker in light mode, brighter in dark mode); a hatched "—" means it doesn't fit. Click a cell to show it in the diagram.
3. **Area breakdown:** stacked bars split into core, imem, FIFOs, units and overhead (grey), labelled with total GE and tiles. Hovering gives the per-class detail; clicking selects the architecture.
4. **imem provisioning:** each family's area as provisioned imem grows. Filled markers are SRAM, hollow are flip-flops. Click a family in the legend to show or hide it. `pio_shared` starts hidden because it is 2–4× larger and would flatten the other lines.
5. **Sensitivity:** the family order at ×0.5 and ×2 for each constant, with ⚠ where the leader changes.

**arch-study.md** has the same results as tables:
- smallest configurations and their area breakdown;
- provisioning;
- utilisation per workload;
- the hand-picked architectures;
- exact vs greedy;
- sensitivity;
- all the inputs (workloads, program demand, constants, the SRAM catalogue).

The **hand-picked architectures** (`architectures.yaml`) are fixed configurations, not sized to the workloads. Use them to try specific designs, such as an RP2040-like 4 × 32-word shared-imem block (`rp2040_block` in the PIO pack).

---

## 11. Changing the study

| To change… | Edit | Then |
|---|---|---|
| protocol mixes or loads | `<pack>/arch/workloads.yaml` (`prog` = a program of the pack) | `uv run pioscope --isa <pack> arch --write` |
| hand-picked architectures | `<pack>/arch/architectures.yaml` (`family: pio` or `mesh`; `imem: flops` or `sram`) | same |
| area constants, unit prices or the SRAM catalogue | `<pack>/arch/cost.yaml` | same |
| a program's demand | the program itself (demand is derived from the assembled image), or the pack's `demand` hooks | same |

`uv run pytest` fails if a pack's `arch-study.md` or `arch-study.html` is stale. Commit the regenerated files with the input change.

---

## 12. What is estimated, and known gaps

From [ADR-0007](open_items/0007-architecture-study.md):

- **Placeholder costs:** the block costs (core, units, FIFO control, link width, config bit) are placeholders until synthesis. Cell and macro areas are sourced.
- **Estimated activity:** activity comes from static bit shares and an assumed load. Phase 2 replaces it with counters measured on the engine model.
- **Pads:** pads are assumed remappable. Only the total (24) is checked; the programs' hard-coded pads are ignored.
- **No contention or latency model:** allocation is exclusive. Per-core vs pooled clock dividers and host-port wiring aren't modelled.
- **SG13CMOS5L availability:** the PDK docs don't say which SRAM macros SG13CMOS5L provides. The catalogue is the SG13G2 list; the Tiny Tapeout example uses `RM_IHPSG13_1P_256x16_c2_bm_bist` on SG13CMOS5L.
- **SRAM fetch latency:** SRAM reads are synchronous, so an SRAM imem needs fetch-ahead or a branch-delay cycle. That is an engine-design decision, not yet modelled.
- **Workload-dependent ranking:** the ranking depends on the workload set.

---

## 13. Glossary

| Term | Meaning |
|---|---|
| active area | The share of total area doing useful work: allocated logic weighted by activity, plus allocated storage (§8) |
| activity | The estimated fraction of cycles a resource is busy: load (core) or load × bit share (unit) |
| allocated area | The share of total area assigned to some program (§8) |
| allocation | The load-time binding of each program to a core, banks, units and FIFOs (§4) |
| bank | A fixed-size block of mesh imem: 2–16 words of flip-flops, or one SRAM macro set |
| bit share | The share of a program's bit-times during which a unit is busy (§5) |
| cluster | A group of mesh cores with their own share of each pool and a local crossbar |
| config bits | Mesh flip-flops holding each core's allocation choices, written at load time |
| core | A core's execution logic: PC, X/Y, OSR/ISR, decode, divider |
| crossbar (xbar) | The mux network that lets cores reach pooled units, FIFOs and banks |
| exact / greedy allocator | A backtracking search that always finds a fit if one exists / first fit in workload order |
| family | One of the five compared organisations: `pio_full`, `pio_lean`, `pio_shared`, `mesh_full`, `mesh_c2` |
| FIFO | A host-side queue: TX (host → core) or RX (core → host); depth in words |
| GE | Gate equivalent: the area of one `sg13g2_nand2_1`, 7.2576 µm² |
| headroom | imem words provisioned per core beyond what current programs need (§7) |
| imem | Instruction memory, built from flip-flops or SRAM macros |
| infeasible | No allocation of a workload exists on an architecture |
| leader | The family whose smallest fitting configuration has the least area |
| load | The fraction of time a protocol's line is busy; an input per program in a workload |
| mesh | The organisation with pooled resources assigned by software (§3.3) |
| overhead | imem read muxes + crossbar + config bits, as a share of total area |
| PIO | Programmable IO (RP2040): fixed state machines with private resources; the baseline organisation |
| read port | One independent memory read per cycle; each fetching core needs one |
| sensitivity | Re-ranking with one placeholder cost halved or doubled (§9) |
| smallest configuration | The cheapest configuration of a family on which every workload fits (§7) |
| SRAM macro | A pre-built memory block from the IHP PDK, e.g. `RM_IHPSG13_1P_64x16_c2` (64 words × 16 bits, 1 port) |
| core | One core plus the imem, FIFOs and units it uses |
| tile | A Tiny Tapeout tile, about 202 × 155 µm ≈ 4,314 GE (counted at 100% density) |
| unit | Hardware attached to a core for per-bit work, declared by a pack in its `cost.yaml` (§2.4) |
| workload | A protocol mix: programs that run on the engine at the same time, each with a load |

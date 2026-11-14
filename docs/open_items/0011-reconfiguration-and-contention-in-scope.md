# ADR-0011: Runtime reconfiguration and contention are in scope for modelling

Status: proposed (2026-11-11). It needs the owner's agreement, because it changes the project's scope.

## Context
The project scope put "multi-context time-sharing of units between protocols" out of scope, and the architecture study (ADR-0007, arch-guide §1) models neither runtime reallocation nor contention. Its allocation is exclusive and fixed at load time.

The architecture decision now needs both:
- whether pooled or shared units pay off depends on how often protocols contend for them;
- a controller that moves resources between protocols at run time could make a smaller engine sufficient.

The tools can now model several cores and shared units without changes (ADR-0010).

## Decision
1. **Modelling** runtime reconfiguration and contention is in scope:
   - shared units with arbiters;
   - a reconfiguration controller as a shared unit;
   - shadow configuration banks swapped under load, where an ISA has them.
2. It stays **out of scope for the ISA** until the models show a need. Anything the models need from the ISA is recorded as a finding first (CLAUDE.md §8 #2, #3).
3. Contention metrics, reported per architecture and per load:
   - stall cycles per core;
   - grant latency (mean and maximum);
   - missed bit deadlines (a symbol late by more than its tolerance);
   - unit utilisation;
   - controller switch count and cost.
4. Unit activity used by the architecture study comes from measured runs (probes and demand traces) where available, replacing `load` estimates.

## Consequences
- The scope (CLAUDE.md) and arch-guide §1 say this.
- Correctness comes first: a contention model must keep every protocol's timing checks passing, or report exactly which deadline it missed.
- Determinism is kept: arbitration policies must be deterministic (round-robin or fixed priority, or seeded if randomised).

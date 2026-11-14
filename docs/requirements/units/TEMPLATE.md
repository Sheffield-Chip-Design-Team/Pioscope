# Unit requirement sheet: <unit name>

**Owner:** … · **Status:** draft | reviewed | agreed · **Driven by:** <protocols>

Copy this file to `docs/requirements/units/<unit>.md`. Each requirement gets an id (`<UNIT>-NN`) and the protocol that needs it. Without a protocol, it isn't a requirement. Write numbers, not adjectives.

## 1. Function
One paragraph: what the unit computes, in hardware terms.

## 2. Interfaces
| Signal / port | Direction | Width | Meaning |
|---|---|---|---|
| bit_in | from the core | 1 | … |

Where it sits relative to the core (between the shift registers and the pins, or beside them) and what it reads from or writes to the core (flags, ISR, pins).

## 3. Configuration
| Field | Width | Range | Set by (directive / instruction) | Needed by |
|---|---|---|---|---|

Are shadow configuration banks needed? Which fields may change mid-frame, and at what boundary?

## 4. Requirements
| Id | Requirement | Needed by | Verified by (test) |
|---|---|---|---|
| <U>-01 | … | CAN | `test_…` |

Include:
- maximum rate (bits per sysclk);
- latency (cycles from input to output);
- per-cycle work, which must be bounded (CLAUDE.md §8 #5);
- state size in bits.

## 5. Sharing and contention
- Can cores share one instance? On what granularity: bit, field, frame?
- State to save or restore on a switch (bits), and the switch cost in cycles.
- What a waiting core sees (stall reason, deadline risk).

## 6. Observability
`state()` fields and `probes()` (with widths) the model exposes (ADR-0010).

## 7. Cost estimate
Gate-equivalent estimate and its basis (the pack's `<pack>/arch/cost.yaml`, synthesis, a similar block).

## 8. Open questions / findings
Links to findings and ADRs.

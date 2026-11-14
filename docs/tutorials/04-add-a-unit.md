# Tutorial 04: add a unit

**You will:** write a hardware unit, attach it to a core of the PIO model, and debug it, without editing the engine core or the tools. **Code:** [`sim/examples/edge_counter.py`](../../sim/examples/edge_counter.py).

## The unit interface

```python
class Unit:
    name: ClassVar[str]                    # unique in the core; used in paths and the VCD
    def state(self) -> dict: ...           # Variables > Units, `info units`
    def probes(self) -> dict[str, int]: ...  # VCD: engine.sm0.units.<name>.<probe>
    def probe_bits(self) -> dict[str, int]: ...
    def on_cycle(self, owner) -> None: ...   # once per sysclk, after the core's work
```

## Read `EdgeCounter`

It reads the level of the first output role from `owner.pins_state()` every cycle and counts changes. It is observe-only: no instruction calls it. Going through `pins_state()` makes it work on any model.

`EdgeCountingEngine` attaches it:

```python
class EdgeCountingEngine(PioEngine):
    def build(self, image):
        super().build(image)                         # one state machine, sm0
        self.cores[0].units.append(EdgeCounter())
```

## Run it

```bash
uv run pioscope debug --scenario sim.examples.pulse_counter:blink_scenario \
    --dut sim.examples.edge_counter:EdgeCountingEngine --vcd build/edges.vcd
(kdb) stepi 1000
(kdb) info units edge_counter      # edges=5
(kdb) watch units.edge_counter.edges
(kdb) continue                     # stops at the next edge
```

In VS Code, add `"dut": "sim.examples.edge_counter:EdgeCountingEngine"` to the Blink configuration from tutorial 01. Open Variables → Units → edge_counter. In GTKWave, the unit has its own sub-group under sm0.

## A unit that instructions use

A unit that instructions drive (a CRC generator, a timer) is also an attribute of the core (`tc.crc`), so that the model's handlers can call it. If a new unit needs a new instruction or a new operand, that is an **ISA change**: follow [tutorial 06](06-isa-change.md) and the rules in [isa-flow.md](../reference/isa-flow.md).

## Exercises
1. Make `EdgeCounter` record the shortest high time it has seen, as a state field and a probe.
2. Write a `BusyMonitor` unit that counts cycles in which `owner.stall` is empty (the SM is doing work). Attach it and measure the busy fraction of `uart_tx` and `spi_m0`. This is the kind of number the architecture study needs instead of its `load` estimates.
3. Write the unit's requirement sheet using [the template](../requirements/units/TEMPLATE.md).

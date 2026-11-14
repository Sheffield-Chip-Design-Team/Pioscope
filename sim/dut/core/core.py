"""The generic core: registers, FIFOs, pins, side-set, delay, wrap, the
clock divider, handler dispatch, and the observability contract (ADR-0010, ADR-0012).

Each sysclk:
  1. inputs pass the 2-flop synchroniser (the engine), giving `pads_seen`;
  2. `before_exec()`: model-specific per-sysclk work (e.g. a timer that ticks);
  3. on a clock-divider enable, the core continues or issues one instruction;
  4. each unit's `on_cycle()`.
An instruction either completes this core cycle or stalls (the PC holds). `[delay]`
adds idle core cycles after it completes; side-set applies when it issues.

A pack's model subclasses `Core` to add flags (`FLAGS`), units (`self.units`),
and per-sysclk work, and passes its instruction handlers (one per instruction in its
yaml, keyed by name).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, ClassVar

from sim.devices.base import Txn
from sim.image import IrInstr, ProgramImage

from .config import SYSCLK_HZ, CoreConfig, Role
from .errors import EngineError
from .fifo import Fifo
from .unit import Unit

M32 = 0xFFFF_FFFF

Handler = Callable[[Any, IrInstr, bool], bool]


def short_src(src: str) -> str:
    """`file:line` with the file relative to the working directory when it is under it."""
    path, sep, line = src.rpartition(":")
    if not sep:
        return src
    try:
        rel = os.path.relpath(path)
    except ValueError:
        return src
    return f"{rel}:{line}" if not rel.startswith("..") else src


def mask(n: int) -> int:
    return (1 << n) - 1 if n < 32 else M32


def registry() -> tuple[dict[str, Handler], Callable[[str], Callable[[Handler], Handler]]]:
    """A handler table and the decorator that fills it (`@handler("jmp")`)."""
    table: dict[str, Handler] = {}

    def handler(name: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            table[name] = fn
            return fn

        return deco

    return table, handler


class Core:
    """One core. `name` (t0, sm0, ...) is how the tools refer to it: the trace source,
    the debugger thread and the VCD scope `engine.<name>`."""

    #: Flag names a model's instructions set and test (shown under Flags).
    FLAGS: ClassVar[tuple[str, ...]] = ()
    #: Probe widths for the core's own registers (units give their own).
    PROBE_BITS: ClassVar[dict[str, int]] = {
        "pc": 5, "x": 32, "y": 32, "osr": 32, "osr_count": 6, "isr": 32, "isr_count": 6,
        "txf": 4, "rxf": 4, "flags": 4, "irq": 8, "stall": 1, "out": 8, "in": 8}
    #: Registers the debugger may write.
    SETTABLE: ClassVar[tuple[str, ...]] = ("x", "y", "osr", "isr", "pc")

    def __init__(self, image: ProgramImage, cfg: CoreConfig, handlers: dict[str, Handler],
                 sysclk_hz: int = SYSCLK_HZ, name: str = "t0") -> None:
        self.name = name
        #: False for a core whose outputs stay internal (e.g. a shadow core).
        self.drives_pads = True
        self.image = image
        self.prog = image.instructions
        if not self.prog:
            raise EngineError(f"program '{image.name}' has no instructions")
        self.cfg = cfg
        self.handlers = handlers
        self.fs = sysclk_hz
        self.labels = {a: n for n, a in image.symbols.items()}
        # architectural state
        self.pc = 0
        self.x = self.y = 0
        self.osr, self.osr_count = 0, 32  # 32 = empty (PIO)
        self.isr, self.isr_count = 0, 0
        self.flags = dict.fromkeys(self.FLAGS, False)
        self.irq = 0
        self.txf, self.rxf = Fifo(cfg.fifo_tx), Fifo(cfg.fifo_rx)
        #: Every unit of this core. The tools iterate over this list, so a unit appended
        #: here shows up in the debugger, the trace and the VCD (ADR-0010).
        self.units: list[Unit] = []
        # pins: output roles drive level + OE; inputs are the synchronised pads
        self.out_level = [cfg.idle] * len(cfg.outputs)
        self.out_oe = [r.mode != "open_drain" for r in cfg.outputs]
        self.pads_seen = [1] * 16  # ui0..7, uio0..7 after the synchroniser
        # execution
        self.cur: IrInstr | None = None
        self.op: dict[str, Any] = {}
        self.delay_left = 0
        self.jumped = False
        self.stall = ""
        self.div_acc = 0
        self.cycle = 0
        self.issued = 0  # instructions issued so far (the debugger's `step`)
        self.events: list[Txn] = []

    # ---- tracing --------------------------------------------------------------------
    def emit(self, kind: str, **data: Any) -> None:
        self.events.append(Txn(self.cycle, kind, data, self.cycle / self.fs))

    def set_flag(self, name: str) -> None:
        if not self.flags[name]:
            self.emit("flag", name=name)
        self.flags[name] = True

    # ---- pins -----------------------------------------------------------------------
    @staticmethod
    def pad_index(r: Role) -> int:
        return r.index if r.group == "ui" else 8 + r.index

    def in_level(self, n: int) -> int:
        """Synchronised level of input role n."""
        ins = self.cfg.inputs
        if n >= len(ins):
            raise EngineError(f"input role {n} does not exist (inputs: "
                              f"{[r.name for r in ins]})", self.src)
        return self.pads_seen[self.pad_index(ins[n])]

    def in_bits(self) -> int:
        return sum(self.in_level(i) << i for i in range(len(self.cfg.inputs)))

    def drive_out(self, n: int, level: int | str) -> None:
        outs = self.cfg.outputs
        if n >= len(outs):
            raise EngineError(f"output role {n} does not exist (outputs: "
                              f"{[r.name for r in outs]})", self.src)
        r = outs[n]
        if level == "z":
            if r.group == "uo":
                raise EngineError(f"'{r.name}' is on {r.pad}, an output-only pad: it cannot "
                                  "be released (z)", self.src)
            self.out_oe[n] = False
            return
        lvl = int(level) & 1
        self.out_level[n] = lvl
        if r.mode == "open_drain":
            self.out_oe[n] = lvl == 0  # drive low, release high
        elif r.group == "uio":
            self.out_oe[n] = True

    def set_out_bits(self, value: int, n: int | None = None) -> None:
        for i in range(len(self.cfg.outputs) if n is None else min(n, len(self.cfg.outputs))):
            self.drive_out(i, (value >> i) & 1)

    def sensed(self, n: int) -> int:
        """Level sensed back on output role n (its own pad if bidirectional, else input 0)."""
        r = self.cfg.outputs[n]
        if r.can_in:
            return self.pads_seen[self.pad_index(r)]
        return self.in_level(0)

    @property
    def src(self) -> str:
        return self.cur.src if self.cur else ""

    # ---- observation (ADR-0010) --------------------------------------------------------
    def regs(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "osr": self.osr, "osr_count": self.osr_count,
                "isr": self.isr, "isr_count": self.isr_count}

    def state(self) -> dict[str, Any]:
        """Everything the debugger shows for this core, as plain data. The scalar keys are
        the ones the debugger relies on (name, pc, label, instr, src, issued,
        in_progress, stall, delay_left, irq); each dict-valued key becomes a scope in
        VS Code (Registers, Flags, FIFOs, Units, Pins, and any a model adds)."""
        cur = self.cur if self.cur is not None else self.prog[self.pc]
        return {
            "name": self.name, "pc": self.pc, "label": self.labels.get(self.pc),
            "instr": cur.text, "src": cur.src, "issued": self.issued,
            "in_progress": self.cur is not None, "stall": self.stall,
            "delay_left": self.delay_left, "irq": self.irq,
            "regs": self.regs(),
            "flags": dict(self.flags),
            "fifo": {"tx": self.txf.state(), "rx": self.rxf.state()},
            "units": {u.name: u.state() for u in self.units},
            "pins": self.pins_state(),
        }

    def pins_state(self) -> dict[str, Any]:
        """The Pins scope: input levels and output level/OE, per `.pins` role."""
        return {
            "in": {r.name: self.pads_seen[self.pad_index(r)] for r in self.cfg.inputs},
            "out": {r.name: {"level": self.out_level[i], "oe": self.out_oe[i]}
                    for i, r in enumerate(self.cfg.outputs)},
        }

    def out_bits(self) -> int:
        """Output levels packed as bits (the `out` probe)."""
        return sum(v << i for i, v in enumerate(self.out_level))

    def probes(self) -> dict[str, int]:
        """Integer signals for the VCD, recorded on change: registers, then each unit's
        probes as `units.<unit>.<probe>`."""
        out = {
            "pc": self.pc, **self.regs(),
            "txf": len(self.txf), "rxf": len(self.rxf),
            "flags": sum(self.flags[f] << i for i, f in enumerate(self.FLAGS)), "irq": self.irq,
            "stall": int(bool(self.stall)),
            "out": self.out_bits(), "in": self.in_bits(),
        }
        for u in self.units:
            out.update({f"units.{u.name}.{k}": v for k, v in u.probes().items()})
        return out

    def probe_bits(self) -> dict[str, int]:
        bits = dict(self.PROBE_BITS)
        for u in self.units:
            bits.update({f"units.{u.name}.{k}": v for k, v in u.probe_bits().items()})
        return bits

    def issue_pc(self) -> int | None:
        """The address that issues on the next cycle, or None (busy, delayed, or the
        divider has no enable). The debugger stops on breakpoints with this."""
        if self.cur is not None or self.delay_left or self.div_acc + 256 < self.cfg.clkdiv:
            return None
        return self.pc

    def set_reg(self, name: str, value: int) -> None:
        """Debugger write: one of SETTABLE (pc only between instructions)."""
        if name not in self.SETTABLE:
            raise EngineError(f"can only set {', '.join(self.SETTABLE)}")
        if name == "pc":
            if self.cur is not None:
                raise EngineError("cannot move the PC while an instruction is in progress")
            if not 0 <= value < len(self.prog):
                raise EngineError(f"address {value} is outside the program "
                                  f"(0..{len(self.prog) - 1})")
            self.pc = value
        else:
            setattr(self, name, value & M32)

    # ---- per sysclk -------------------------------------------------------------------
    def sysclk(self) -> None:
        """Steps 2-4 of the cycle (the synchroniser has already updated pads_seen)."""
        self.before_exec()
        if self._enabled():
            self._exec()
        for u in self.units:
            u.on_cycle(self)
        self.cycle += 1

    def before_exec(self) -> None:
        """Model-specific work every sysclk, before the core's own (override)."""

    def _enabled(self) -> bool:
        self.div_acc += 256
        if self.div_acc >= self.cfg.clkdiv:
            self.div_acc -= self.cfg.clkdiv
            return True
        return False

    def fetch(self) -> IrInstr:
        """The instruction to issue now (override for an injected one, e.g. PIO exec)."""
        return self.prog[self.pc]

    def next_pc(self) -> int:
        """The PC after a non-jumping instruction at self.pc (with .wrap)."""
        return self.cfg.wrap_target if self.pc == self.cfg.wrap else (
            (self.pc + 1) % len(self.prog))

    def _exec(self) -> None:
        if self.delay_left:
            self.delay_left -= 1
            self.stall = "delay"
            return
        first = self.cur is None
        if first:
            self.cur = self.fetch()
            self.op = {}
            self.jumped = False
            self.issued += 1
            self.side_set(self.cur)
            self.emit("exec", pc=self.pc, op=self.cur.text, src=short_src(self.cur.src))
        ins = self.cur
        assert ins is not None
        done = self.handlers[ins.name](self, ins, first)
        self.post_handler(ins, first)
        if not done:
            reason = self.op.get("stall", ins.name)
            if self.stall != reason:
                self.emit("stall", pc=self.pc, reason=reason)
            self.stall = reason
            return
        self.stall = ""
        self.cur = None
        self.retire(ins)

    def post_handler(self, ins: IrInstr, first: bool) -> None:
        """After the handler ran this cycle, done or stalled (override)."""

    def retire(self, ins: IrInstr) -> None:
        """After `ins` completes: advance the PC (unless it jumped) and start its delay."""
        if not self.jumped:
            self.pc = self.next_pc()
        self.delay_left = ins.delay

    def side_set(self, ins: IrInstr) -> None:
        c = self.cfg
        if not c.side_count or ins.side is None:
            return
        for i in range(c.side_count):
            n = c.side_base + i
            bit = (ins.side >> i) & 1
            if c.side_pindirs:
                self.out_oe[n] = bool(bit)
            else:
                self.drive_out(n, bit)

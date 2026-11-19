"""RP2040 PIO model: a PIO block with its state machines, on the generic core.

`PioEngine` implements the DUT contract (`piolab --isa pio sim uart_tx -m base`). It
runs one state machine (`sm0`) per program, and its block-level state is two shared
units, so the debugger, trace and VCD show them (ADR-0010):

- `gpio`: the output levels and directions of GPIO 0-23 (ui0-7 = 0-7, uo0-7 = 8-15,
  uio0-7 = 16-23). Every SM writes here; a uo pad is always driven, a uio pad when its
  direction bit is 1.
- `irq`: the block's eight IRQ flags. Flags 0-3 are the host's interrupt lines.

Semantics are piolab/packs/pio/isa.yaml's (from the RP2040 datasheet, chapter 3), with these
modelling choices:
- one SM per program (a block has four; a multi-SM model fills `cores` with more);
- inputs pass the 2-flop synchroniser; a uo pad reads back its driven level;
- side-set takes priority over an `out`/`set`/`mov` write to the same pin in the
  same cycle;
- an `out exec` / `mov exec` runs the word on the next SM cycle without moving the PC,
  and the delay of the `out`/`mov` itself is ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from piolab.bench.pads import PadOut
from piolab.dut.core import Core, CoreConfig, EngineError, Handler, Unit
from piolab.dut.core import Engine as CoreEngine
from piolab.image import IrInstr, ProgramImage
from piolab.isa import Isa, load_isa
from piolab.packs.pio.pins import NUM_GPIO, gpio_of

from .exec import HANDLERS

__all__ = ["HANDLERS", "Gpio", "IrqFlags", "PioConfig", "PioEngine", "PioSm"]

GPIO_MASK = (1 << NUM_GPIO) - 1


def _default_sm(image: ProgramImage) -> dict[str, Any]:
    """`config["sm"]` for an image assembled without one (all defaults)."""
    pins = image.config.get("pins", {})
    outs = [gpio_of(p["pad"]) for p in pins.values() if p.get("dir") in ("out", "bidir")]
    ins = [gpio_of(p["pad"]) for p in pins.values() if p.get("dir") in ("in", "bidir")]
    o, i = (outs[0] if outs else 0), (ins[0] if ins else 0)
    return {"out": {"base": o, "count": 1}, "set": {"base": o, "count": 1}, "in": i,
            "sideset": o, "jmp": i, "out_shift": "right", "in_shift": "right",
            "autopull": False, "pull_thresh": 32, "autopush": False, "push_thresh": 32,
            "fifo": "none", "status": {"fifo": "tx", "n": 0}, "high": []}


@dataclass
class PioConfig(CoreConfig):
    sm: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_image(cls, image: ProgramImage, fifo_depth: int = 4) -> Any:
        cfg = super().from_image(image, fifo_depth)
        cfg.sm = dict(image.config.get("sm") or _default_sm(image))
        join = cfg.sm.get("fifo", "none")
        if join == "tx":
            cfg.fifo_tx, cfg.fifo_rx = 2 * fifo_depth, 0
        elif join == "rx":
            cfg.fifo_tx, cfg.fifo_rx = 0, 2 * fifo_depth
        return cfg


class Gpio(Unit):
    """The block's pin outputs (shared by its SMs)."""

    name = "gpio"

    def __init__(self) -> None:
        self.out = 0
        self.oe = 0

    def write(self, base: int, count: int, value: int, dirs: bool = False) -> None:
        for i in range(count):
            g = (base + i) % 32
            if g >= NUM_GPIO:
                continue
            bit = (value >> i) & 1
            if dirs:
                self.oe = (self.oe & ~(1 << g)) | (bit << g)
            else:
                self.out = (self.out & ~(1 << g)) | (bit << g)

    def state(self) -> dict[str, Any]:
        return {"out": f"0x{self.out:06x}", "oe": f"0x{self.oe:06x}"}

    def probes(self) -> dict[str, int]:
        return {"out": self.out, "oe": self.oe}

    def probe_bits(self) -> dict[str, int]:
        return {"out": NUM_GPIO, "oe": NUM_GPIO}


class IrqFlags(Unit):
    """The block's eight IRQ flags (shared by its SMs; 0-3 reach the host)."""

    name = "irq"

    def __init__(self) -> None:
        self.flags = 0

    def state(self) -> dict[str, Any]:
        return {"flags": f"0b{self.flags:08b}"}

    def probes(self) -> dict[str, int]:
        return {"flags": self.flags}

    def probe_bits(self) -> dict[str, int]:
        return {"flags": 8}


class PioSm(Core):
    """One PIO state machine."""

    PROBE_BITS: ClassVar[dict[str, int]] = {**Core.PROBE_BITS, "out": NUM_GPIO,
                                            "in": NUM_GPIO}
    cfg: PioConfig

    def __init__(self, image: ProgramImage, cfg: PioConfig, handlers: dict[str, Handler],
                 sysclk_hz: int, name: str, block: PioEngine, index: int = 0) -> None:
        super().__init__(image, cfg, handlers, sysclk_hz, name)
        self.block = block
        self.index = index
        self.exec_next: IrInstr | None = None  # from out exec / mov exec
        self.executing = False  # the current instruction came from exec
        self.skip_delay = False
        self._side: IrInstr | None = None

    # ---- the SM's view of the block ----------------------------------------------------
    @property
    def sm(self) -> dict[str, Any]:
        return self.cfg.sm

    @property
    def isa(self) -> Isa:
        return self.block.isa

    def gpio_in(self, g: int) -> int:
        """Level of GPIO g as the SM sees it: synchronised pad inputs; a uo pad reads
        back its driven level; GPIOs past the tile read 0."""
        g %= 32
        if g < 8:
            return self.pads_seen[g]
        if g < 16:
            return (self.block.gpio.out >> g) & 1
        if g < NUM_GPIO:
            return self.pads_seen[g - 8]
        return 0

    def read_pins(self) -> int:
        """32 bits from the in base upwards (wrapping), as `in pins` / `mov x, pins`."""
        base = int(self.sm["in"])
        return sum(self.gpio_in(base + i) << i for i in range(32))

    def write_pins(self, group: str, value: int, dirs: bool = False) -> None:
        g = self.sm[group]
        self.block.gpio.write(int(g["base"]), int(g["count"]), value, dirs)

    @property
    def irq(self) -> int:
        return self.block.irqs.flags

    @irq.setter
    def irq(self, v: int) -> None:
        if hasattr(self, "block"):
            self.block.irqs.flags = v & 0xFF

    def irq_index(self, n: int, rel: bool) -> int:
        return (n & 4) | ((n + self.index) & 3) if rel else n

    # ---- core hooks --------------------------------------------------------------------
    def fetch(self) -> IrInstr:
        if self.exec_next is not None:
            ins, self.exec_next = self.exec_next, None
            self.executing = True
            return ins
        self.executing = False
        return self.prog[self.pc]

    def side_set(self, ins: IrInstr) -> None:
        self._side = ins  # applied after the handler: side-set wins over out/set/mov

    def post_handler(self, ins: IrInstr, first: bool) -> None:
        side = self._side
        self._side = None
        count = self.cfg.side_count
        if side is None or side.side is None or not count:
            return
        self.block.gpio.write(int(self.sm["sideset"]), count, side.side,
                              dirs=self.cfg.side_pindirs)

    def retire(self, ins: IrInstr) -> None:
        if not self.jumped and not self.executing:
            self.pc = self.next_pc()
        self.delay_left = 0 if self.skip_delay else ins.delay
        self.skip_delay = False

    def next_pc(self) -> int:
        if self.pc == self.cfg.wrap:
            return self.cfg.wrap_target
        return (self.pc + 1) % len(self.prog)

    def queue_exec(self, word: int) -> None:
        """`out exec` / `mov exec`: run `word` as the next instruction."""
        from piolab.packs.pio.encode import decode_word, disassemble

        name, ops, side, delay = decode_word(word, self.isa, 32, self.image.config)
        cur = self.cur
        self.exec_next = IrInstr(addr=self.pc, name=name, operands=ops, side=side, delay=delay,
                                 src=cur.src if cur else "",
                                 text=f"(exec 0x{word & 0xFFFF:04x}) "
                                      f"{disassemble(word, self.isa, self.image.config)}")
        self.skip_delay = True

    def jump(self, addr: int) -> None:
        if not 0 <= addr < len(self.prog):
            raise EngineError(f"jump to {addr}: the program has {len(self.prog)} "
                              "instructions", self.src)
        self.pc, self.jumped = addr, True

    # ---- observation -------------------------------------------------------------------
    def in_bits(self) -> int:
        return sum(self.gpio_in(g) << g for g in range(NUM_GPIO))

    def out_bits(self) -> int:
        return self.block.gpio.out & GPIO_MASK

    def pins_state(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        ins: dict[str, int] = {}
        gp = self.block.gpio
        for r in self.cfg.roles:
            g = gpio_of(r.pad)
            if r.can_in:
                ins[r.name] = self.gpio_in(g)
            if r.can_out:
                out[r.name] = {"level": (gp.out >> g) & 1, "oe": bool((gp.oe >> g) & 1)
                               if r.group == "uio" else True}
        return {"in": ins, "out": out}

    def state(self) -> dict[str, Any]:
        st = super().state()
        st["config"] = {
            "out": f"{self.sm['out']['base']}:{self.sm['out']['count']}",
            "set": f"{self.sm['set']['base']}:{self.sm['set']['count']}",
            "in": self.sm["in"], "sideset": self.sm["sideset"], "jmp": self.sm["jmp"],
            "out_shift": self.sm["out_shift"], "in_shift": self.sm["in_shift"],
            "autopull": self.sm["pull_thresh"] if self.sm["autopull"] else "off",
            "autopush": self.sm["push_thresh"] if self.sm["autopush"] else "off",
        }
        return st


class PioEngine(CoreEngine):
    """A PIO block on the Tiny Tapeout pads."""

    def build(self, image: ProgramImage) -> None:
        self.isa = load_isa(_spec())
        cfg = PioConfig.from_image(image, int(self.isa.params["fifo_depth"]))
        self.gpio = Gpio()
        self.irqs = IrqFlags()
        for r in cfg.roles:  # as the SDK's pin init would: outputs drive, open-drain floats
            if r.can_out:
                g = gpio_of(r.pad)
                self.gpio.oe |= int(r.mode != "open_drain") << g
        for g in cfg.sm.get("high", []):
            self.gpio.out |= 1 << int(g)
        self.shared = [self.gpio, self.irqs]
        self.cores = [PioSm(image, cfg, HANDLERS, self.fs, "sm0", self, 0)]

    def host_irq(self) -> int:
        return self.irqs.flags & 0x0F

    def _pads(self) -> PadOut:
        if not self.cores:
            raise EngineError("no program loaded: the engine needs an image")
        out, oe = self.gpio.out, self.gpio.oe
        return PadOut(uo=(out >> 8) & 0xFF, uio_out=(out >> 16) & 0xFF,
                      uio_oe=(oe >> 16) & 0xFF)


def _spec() -> Any:
    from piolab.packs import get

    return get("pio").spec


"""PIO instruction handlers: exactly one per instruction in spec/isa/pio.yaml, keyed by
its name (sim/packs/pio/tests check the set).

A handler is called with (sm, instruction, first) on each SM cycle the instruction is
current; it returns True when the instruction completes, False to stall (the PC holds;
`sm.op["stall"]` says why).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sim.dut.core import M32, EngineError, mask, registry
from sim.image import IrInstr

if TYPE_CHECKING:
    from . import PioSm

HANDLERS, handler = registry()


def _bitrev(v: int) -> int:
    return int(f"{v & M32:032b}"[::-1], 2)


def _source(sm: PioSm, src: str) -> int:
    if src == "pins":
        return sm.read_pins()
    if src == "x":
        return sm.x
    if src == "y":
        return sm.y
    if src == "null":
        return 0
    if src == "isr":
        return sm.isr
    if src == "osr":
        return sm.osr
    if src == "status":
        st = sm.sm["status"]
        level = len(sm.txf if st["fifo"] == "tx" else sm.rxf)
        return M32 if level < int(st["n"]) else 0
    raise EngineError(f"unknown source '{src}'", sm.src)


def _write(sm: PioSm, dst: str, v: int, isr_count: int) -> None:
    """Write v to dst for `out` and `mov`. `isr_count` is the input shift count a write
    to ISR leaves: the bit count for `out isr, n`, 0 for `mov isr`."""
    v &= M32
    if dst == "pins":
        sm.write_pins("out", v)
    elif dst == "pindirs":
        sm.write_pins("out", v, dirs=True)
    elif dst == "x":
        sm.x = v
    elif dst == "y":
        sm.y = v
    elif dst == "null":
        pass
    elif dst == "pc":
        sm.jump(v % 32)
    elif dst == "isr":
        sm.isr, sm.isr_count = v, isr_count
    elif dst == "osr":
        sm.osr, sm.osr_count = v, 0
    elif dst == "exec":
        sm.queue_exec(v)
    else:
        raise EngineError(f"unknown destination '{dst}'", sm.src)


def _cond(sm: PioSm, cond: str) -> bool:
    if cond == "always":
        return True
    if cond == "!x":
        return sm.x == 0
    if cond == "x--":
        r = sm.x != 0
        sm.x = (sm.x - 1) & M32
        return r
    if cond == "!y":
        return sm.y == 0
    if cond == "y--":
        r = sm.y != 0
        sm.y = (sm.y - 1) & M32
        return r
    if cond == "x!=y":
        return sm.x != sm.y
    if cond == "pin":
        return bool(sm.gpio_in(int(sm.sm["jmp"])))
    if cond == "!osre":
        return sm.osr_count < int(sm.sm["pull_thresh"])
    raise EngineError(f"unknown condition '{cond}'", sm.src)


@handler("jmp")
def _jmp(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    if _cond(sm, ins.operands["cond"]):
        sm.jump(int(ins.operands["target"]))
    return True


@handler("wait")
def _wait(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    o = ins.operands
    pol, src, idx = int(o["pol"]), o["src"], int(o["idx"])
    if src == "gpio":
        hit = sm.gpio_in(idx) == pol
    elif src == "pin":
        hit = sm.gpio_in(int(sm.sm["in"]) + idx) == pol
    elif src == "irq":
        n = sm.irq_index(idx, bool(o["rel"]))
        hit = (sm.irq >> n & 1) == pol
        if hit and pol:
            sm.irq &= ~(1 << n)  # a satisfied `wait 1 irq` clears the flag
    else:
        raise EngineError(f"wait {src}: unknown source", sm.src)
    if not hit:
        sm.op["stall"] = f"wait {pol} {src} {idx}"
    return hit


def _shift_in(sm: PioSm, data: int, n: int) -> tuple[int, int]:
    data &= mask(n)
    if n == 32:
        isr = data
    elif sm.sm["in_shift"] == "right":
        isr = (sm.isr >> n) | (data << (32 - n))
    else:
        isr = (sm.isr << n) | data
    return isr & M32, min(32, sm.isr_count + n)


@handler("in")
def _in(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    n = int(ins.operands["count"])
    isr, count = _shift_in(sm, _source(sm, ins.operands["src"]), n)
    if sm.sm["autopush"] and count >= int(sm.sm["push_thresh"]):
        if sm.rxf.full:
            sm.op["stall"] = "autopush: rx fifo full"
            return False
        sm.rxf.push(isr)
        sm.emit("push", word=isr, auto=True)
        sm.isr, sm.isr_count = 0, 0
        return True
    sm.isr, sm.isr_count = isr, count
    return True


def _refill(sm: PioSm) -> bool:
    w = sm.txf.pop()
    if w is None:
        return False
    sm.osr, sm.osr_count = w, 0
    sm.emit("pull", word=w, auto=True)
    return True


@handler("out")
def _out(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    n = int(ins.operands["count"])
    thresh = int(sm.sm["pull_thresh"])
    if sm.sm["autopull"] and sm.osr_count >= thresh and not _refill(sm):
        sm.op["stall"] = "autopull: tx fifo empty"
        return False
    if sm.sm["out_shift"] == "right":
        data = sm.osr & mask(n)
        sm.osr = 0 if n == 32 else sm.osr >> n
    else:
        data = (sm.osr >> (32 - n)) & mask(n)
        sm.osr = 0 if n == 32 else (sm.osr << n) & M32
    sm.osr_count = min(32, sm.osr_count + n)
    _write(sm, ins.operands["dst"], data, n)
    if sm.sm["autopull"] and sm.osr_count >= thresh:
        _refill(sm)  # refill in the background when data is waiting
    return True


@handler("push")
def _push(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    o = ins.operands
    if o["iffull"] and sm.isr_count < int(sm.sm["push_thresh"]):
        return True
    if sm.rxf.full:
        if o["mode"] == "block":
            sm.op["stall"] = "rx fifo full"
            return False
        sm.emit("push", word=sm.isr, dropped=True)
    else:
        sm.rxf.push(sm.isr)
        sm.emit("push", word=sm.isr)
    sm.isr, sm.isr_count = 0, 0
    return True


@handler("pull")
def _pull(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    o = ins.operands
    if sm.sm["autopull"] and sm.osr_count == 0:
        return True  # with autopull, a pull is a no-op while OSR is full
    if o["ifempty"] and sm.osr_count < int(sm.sm["pull_thresh"]):
        return True
    w = sm.txf.pop()
    if w is None:
        if o["mode"] == "block":
            sm.op["stall"] = "tx fifo empty"
            return False
        w = sm.x  # a non-blocking pull from an empty FIFO copies X
    sm.osr, sm.osr_count = w, 0
    sm.emit("pull", word=w)
    return True


@handler("mov")
def _mov(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    src: dict[str, Any] = ins.operands["src"]
    v = _source(sm, src["src"])
    if src["op"] in ("!", "~"):
        v = ~v & M32
    elif src["op"] == "::":
        v = _bitrev(v)
    _write(sm, ins.operands["dst"], v, 0)
    return True


@handler("nop")
def _nop(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    return True


@handler("irq")
def _irq(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    o = ins.operands
    n = sm.irq_index(int(o["n"]), bool(o["rel"]))
    if o["op"] == "clear":
        sm.irq &= ~(1 << n)
        return True
    if first:
        sm.irq |= 1 << n
        sm.emit("irq", op=o["op"], n=n)
    if o["op"] == "wait" and sm.irq >> n & 1:
        sm.op["stall"] = f"irq {n} set"
        return False
    return True


@handler("set")
def _set(sm: PioSm, ins: IrInstr, first: bool) -> bool:
    dst, v = ins.operands["dst"], int(ins.operands["value"])
    if dst == "pins":
        sm.write_pins("set", v)
    elif dst == "pindirs":
        sm.write_pins("set", v, dirs=True)
    elif dst == "x":
        sm.x = v
    elif dst == "y":
        sm.y = v
    else:
        raise EngineError(f"unknown set destination '{dst}'", sm.src)
    return True

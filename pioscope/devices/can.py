"""CAN 2.0 node: a bus partner on one open-drain line (drive 0 = dominant, Z = recessive).

The node works like a real CAN controller, in two layers, because what it sends
depends on what it reads back mid-frame (arbitration, ACK, error flags):

- **bit timing:** the node's own clock (bitrate, ppm) counted in time quanta.
  Hard sync on a recessive -> dominant edge while the bus is idle (start of frame);
  resync on later edges, at most once per bit and by at most `sjw` quanta. A node
  doesn't resync on an edge it caused itself.
- **bit stream:** at each bit start the node drives its next bit; at the sample point
  it reads the bus. It always receives (its own frames too). While transmitting it
  compares sent and read bits: recessive sent and dominant read on an arbitration
  field bit loses arbitration; anywhere else (stuff bits included, as in
  ISO 11898-1) it is a bit error.

Fault confinement follows ISO 11898-1 in simplified form: TEC/REC, error-active /
error-passive / bus-off, the passive transmitter's ACK-error exception, suspend
transmission, and bus-off recovery after 128 x 11 recessive bits. Not modelled: CAN
FD, overload frames, the +8 REC rule for dominant bits after an error flag.

Ports:
    bus     the CAN line (open drain; the board adds the pull-up and the wired-AND)

Logged transactions:
    tx_start   {id, attempt}                         this node starts a frame (SOF)
    sent       {id, ext, rtr, dlc, data, attempt}    a frame it sent completed
    tx_abort   {id, attempts, tec, rec}              gave up after `retransmit` retries
    arb_lost   {id, bit}                             lost arbitration at frame bit `bit`
    rx         {id, ext, rtr, dlc, data, start}      a valid frame received (not its own)
    ack        {}                                    it drove the ACK slot
    error      {type, bit, role}                     type: bit|stuff|crc|form|ack
    error_flag {passive}                             it starts an error flag
    state      {state, tec, rec}                     error_active|error_passive|bus_off

`data` is a list of byte values; `bit` counts de-stuffed frame bits, SOF = 0.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pioscope.wave import Z

from .base import Device

_POLY = 0x4599
_SCALE = 1 << 32  # bit-timing position: integer units per time quantum


@dataclass(frozen=True)
class CanTiming:
    bitrate: float
    tq_per_bit: int = 10  # sync (1) + prop + phase1 + phase2
    sample_tq: int = 8  # sample after this many quanta (8 of 10 = 80 %)
    sjw: int = 1  # resync jump width, in quanta
    ppm: float = 0.0  # node clock error; + = node is fast
    offset_s: float = 0.0  # phase of the node's bit clock at cycle 0

    def __post_init__(self) -> None:
        if not 1 <= self.sample_tq < self.tq_per_bit:
            raise ValueError("sample_tq must be in 1 .. tq_per_bit-1")
        if not 1 <= self.sjw <= min(4, self.tq_per_bit - self.sample_tq):
            raise ValueError("sjw must be 1..4 and no longer than phase segment 2")


@dataclass(frozen=True)
class CanFrame:
    id: int
    data: bytes = b""
    ext: bool = False
    rtr: bool = False
    dlc: int | None = None  # default len(data); 9..15 is sent as such (8 data bytes)

    def __post_init__(self) -> None:
        if not 0 <= self.id < (1 << (29 if self.ext else 11)):
            raise ValueError(f"id {self.id:#x} out of range")
        if len(self.data) > 8:
            raise ValueError("at most 8 data bytes")
        if self.dlc is not None and not 0 <= self.dlc <= 15:
            raise ValueError("dlc must be 0..15")

    @property
    def length(self) -> int:
        return len(self.data) if self.dlc is None else self.dlc

    @property
    def payload(self) -> bytes:
        return b"" if self.rtr else self.data[: min(self.length, 8)].ljust(
            min(self.length, 8), b"\0")


@dataclass(frozen=True)
class CanSend:
    """One frame to send, with optional fault injection. Faults apply to the first
    `fault_attempts` attempts; retransmissions after that are clean."""

    frame: CanFrame
    gap_bits: float = 0  # extra idle bits before this frame may start
    crc_error: bool = False  # invert the last CRC bit
    stuff_error_at: int | None = None  # n-th stuff bit sent with the wrong value
    flip_bit: int | None = None  # invert the n-th bit on the wire (stuffed, SOF = 0)
    form_error: str | None = None  # "crc_delim" | "ack_delim" | "eof"
    fault_attempts: int = 1

    def __post_init__(self) -> None:
        if self.form_error not in (None, "crc_delim", "ack_delim", "eof"):
            raise ValueError("form_error must be crc_delim, ack_delim or eof")


def _crc15(bits: Sequence[int]) -> int:
    reg = 0
    for b in bits:
        fb = b ^ (reg >> 14)
        reg = (reg << 1) & 0x7FFF
        if fb:
            reg ^= _POLY
    return reg


def _msb(value: int, n: int) -> list[int]:
    return [(value >> (n - 1 - i)) & 1 for i in range(n)]


def _header(f: CanFrame) -> list[int]:
    if f.ext:
        bits = [0, *_msb(f.id >> 18, 11), 1, 1, *_msb(f.id & 262143, 18), int(f.rtr), 0, 0]
    else:
        bits = [0, *_msb(f.id, 11), int(f.rtr), 0, 0]
    return bits + _msb(f.length, 4)


@dataclass
class _Tx:
    """A frame on the wire: stuffed bits SOF..EOF, plus where things are."""

    send: CanSend
    attempt: int
    bits: list[int]  # wire bits, stuffed, with the 10-bit tail
    frame_bit: list[int]  # wire index -> de-stuffed frame bit index (stuff bits: previous)
    arb_end: int  # last wire index of the arbitration field (the RTR bit)
    ack_slot: int  # wire index of the ACK slot
    stuff: frozenset[int]  # wire indices of stuff bits
    i: int = -1  # wire index of the bit being sent


def _build_tx(send: CanSend, attempt: int) -> _Tx:
    f = send.frame
    faulty = attempt <= send.fault_attempts
    body = _header(f) + [b for byte in f.payload for b in _msb(byte, 8)]
    crc = _crc15(body) ^ (1 if faulty and send.crc_error else 0)
    body += _msb(crc, 15)
    wire: list[int] = []
    fbit: list[int] = []
    stuff_at: list[int] = []
    run, last = 0, -1
    for k, b in enumerate(body):
        wire.append(b)
        fbit.append(k)
        run, last = (run + 1, b) if b == last else (1, b)
        if run == 5:
            stuff_at.append(len(wire))
            wire.append(1 - b)
            fbit.append(k)
            run, last = 1, 1 - b
    if faulty and send.stuff_error_at is not None:
        if send.stuff_error_at >= len(stuff_at):
            raise ValueError(f"frame has only {len(stuff_at)} stuff bits")
        p = stuff_at[send.stuff_error_at]
        wire[p] = wire[p - 1]
    tail = [1, 1, 1] + [1] * 7  # CRC delimiter, ACK slot, ACK delimiter, EOF
    if faulty and send.form_error:
        tail[{"crc_delim": 0, "ack_delim": 2, "eof": 3}[send.form_error]] = 0
    ack_slot = len(wire) + 1
    fbit += [len(body) + j for j in range(len(tail))]
    wire += tail
    if faulty and send.flip_bit is not None:
        wire[send.flip_bit] ^= 1
    rtr_bit = 32 if f.ext else 12
    arb_end = max(i for i, k in enumerate(fbit) if k == rtr_bit)
    return _Tx(send, attempt, wire, fbit, arb_end, ack_slot, frozenset(stuff_at))


@dataclass
class _Rx:
    """De-stuffing receiver state for one frame."""

    start: int
    bits: list[int] = field(default_factory=list)
    run: int = 0
    last: int = -1
    stuffing: bool = True
    hdr: int = 0  # header length once IDE is known (19 or 39)
    need: int | None = None  # de-stuffed bits up to the end of the CRC
    crc_ok: bool = False
    tail: int = 0
    dead: bool = False  # stopped after an error in a frame this node was sending


def _num(bits: Sequence[int]) -> int:
    v = 0
    for b in bits:
        v = (v << 1) | b
    return v


class CanNode(Device):
    ports = ("bus",)

    def __init__(
        self,
        name: str,
        timing: CanTiming,
        send: Iterable[CanFrame | CanSend] = (),
        ack: bool = True,
        retransmit: int | None = 8,  # retries after errors; None = forever (as ISO)
        start_delay_bits: float = 0.0,
        glitches: Sequence[tuple[int, int]] = (),  # (cycle, width): pull the bus low
        bus_off_recovery: bool = True,
        seed: int = 0,
    ) -> None:
        super().__init__(name, seed)
        self.timing = timing
        self.queue = [s if isinstance(s, CanSend) else CanSend(s) for s in send]
        self.ack = ack
        self.retransmit = retransmit
        self.start_delay_bits = start_delay_bits
        self.glitches = list(glitches)
        self.bus_off_recovery = bus_off_recovery
        self.tec = 0
        self.rec = 0
        self.state = "error_active"
        # bit timing
        self._pos = 0
        self._inc = 0
        self._sampled = False
        self._synced = False
        self._prev = 1
        self._drv = 1
        # bit stream
        self.mode = "integrate"
        self._n = 0  # bit counter of the current mode
        self._seq = 0  # bus-off recovery: sequences of 11 recessive bits
        self._rx: _Rx | None = None
        self.tx: _Tx | None = None
        self._attempt = 0
        self._fails = 0
        self._ack_next = False
        self._flag_active = True
        self._ack_exception = False
        self._suspend_pending = False
        self._not_before = 0  # earliest cycle to start the next frame

    # ---- setup ------------------------------------------------------------------
    def on_attach(self) -> None:
        t = self.timing
        rate = t.bitrate * (1 + t.ppm * 1e-6)  # bits per second on the node's clock
        self._inc = round(t.tq_per_bit * rate / self.sysclk_hz * _SCALE)
        phase = (t.offset_s * rate) % 1.0
        self._pos = round(phase * t.tq_per_bit * _SCALE)
        self._bit_cycles = self.sysclk_hz / rate
        self._not_before = round(self.start_delay_bits * self._bit_cycles)

    @property
    def quiet(self) -> bool:
        """Nothing left to send and the bus idle (for scenario stop conditions)."""
        return not self.queue and self.mode == "idle"

    # ---- per cycle: bit timing ------------------------------------------------------
    def tick(self) -> None:
        lvl = self.read("bus")
        if lvl not in (0, 1):
            lvl = self._prev
        if self._prev == 1 and lvl == 0:
            self._edge()
        self._prev = lvl
        t = self.timing
        self._pos += self._inc
        while True:
            if not self._sampled and self._pos >= t.sample_tq * _SCALE:
                self._sampled = True
                self._synced = False
                self._sample(lvl)
            elif self._pos >= t.tq_per_bit * _SCALE:
                self._pos -= t.tq_per_bit * _SCALE
                self._sampled = False
                self._bit_start()
            else:
                break
        glitch = any(c <= self.cycle < c + w for c, w in self.glitches)
        self.drive("bus", 0 if self._drv == 0 or glitch else Z)

    def _edge(self) -> None:
        t = self.timing
        if self.mode in ("idle", "intermission", "suspend"):
            self._pos = 0  # hard sync: this edge starts a frame
            self._sampled = False
            self._synced = True
            self._bit_start(sof=True)
            return
        if self.mode in ("integrate", "bus_off") or self._synced or self._drv == 0:
            return
        q = self._pos // _SCALE
        if q == 0:
            pass
        elif not self._sampled:  # late edge: lengthen phase segment 1
            self._pos -= min(q, t.sjw) * _SCALE
        else:  # early edge (next bit's sync segment): shorten phase segment 2
            self._pos += min(t.tq_per_bit - q, t.sjw) * _SCALE
        self._synced = True

    # ---- bit stream -------------------------------------------------------------------
    def _can_start(self) -> bool:
        return bool(self.queue) and self.cycle >= self._not_before and self.state != "bus_off"

    def _bit_start(self, sof: bool = False) -> None:
        if sof:
            suspended = self.mode == "suspend"
            self._enter_frame()
            if not suspended and self._can_start():
                self._start_tx()
        elif self.mode == "idle" and self._can_start():
            self._enter_frame()
            self._start_tx()
        acking, self._ack_next = self._ack_next, False
        drv = 1
        if self.mode == "frame":
            if self.tx is not None:
                self.tx.i += 1
                drv = self.tx.bits[self.tx.i]
            elif acking:
                drv = 0
                self.emit("ack")
        elif self.mode == "eflag" and self._flag_active:
            drv = 0
        self._drv = drv

    def _enter_frame(self) -> None:
        self.mode = "frame"
        self._rx = _Rx(start=self.cycle)

    def _start_tx(self) -> None:
        self._attempt += 1
        self.tx = _build_tx(self.queue[0], self._attempt)
        self.emit("tx_start", id=self.queue[0].frame.id, attempt=self._attempt)

    def _sample(self, lvl: int) -> None:
        m = self.mode
        if m == "integrate":
            self._n = self._n + 1 if lvl else 0
            if self._n >= 11:
                self.mode = "idle"
        elif m == "bus_off":
            self._n = self._n + 1 if lvl else 0
            if self._n == 11:
                self._n = 0
                self._seq += 1
                if self._seq == 128 and self.bus_off_recovery:
                    self.tec = self.rec = 0
                    self._update_state()
                    self.mode = "idle"
        elif m == "intermission":
            self._n += 1
            if self._n == 3:
                self.mode, self._n = ("suspend", 0) if self._suspend_pending else ("idle", 0)
                self._suspend_pending = False
        elif m == "suspend":
            self._n += 1
            if self._n == 8:
                self.mode = "idle"
        elif m == "frame":
            self._frame_sample(lvl)
        elif m == "eflag":
            if self._ack_exception and lvl == 0:
                self._ack_exception = False
                self.tec += 8
                if self._update_state():
                    return
            self._n += 1
            if self._n == 6:
                self.mode, self._n = "edelim", 0
                self._ack_exception = False
        elif m == "edelim":
            self._n = self._n + 1 if lvl else 0
            if self._n == 8:
                self.mode, self._n = "intermission", 0

    def _frame_sample(self, lvl: int) -> None:
        tx = self.tx
        if tx is not None:
            sent, w = tx.bits[tx.i], tx.i
            if sent == 1 and lvl == 0 and w <= tx.arb_end and w not in tx.stuff:
                assert self._rx is not None
                if self._rx.dead:  # our own (injected) fault: we can't follow as a receiver
                    self._error("bit")
                    return
                self.emit("arb_lost", id=tx.send.frame.id, bit=tx.frame_bit[w])
                self.tx = None
            elif w == tx.ack_slot:
                if lvl == 1:
                    self._error("ack")
                    return
            elif sent != lvl:
                self._error("bit")
                return
        self._parse(lvl)
        if tx is not None and self.tx is tx and tx.i == len(tx.bits) - 1:
            self._tx_done()

    def _parse(self, lvl: int) -> None:
        p = self._rx
        assert p is not None
        if p.dead:
            return
        if not p.bits and p.run == 0 and lvl == 1:  # SOF sampled recessive: a glitch
            self.mode = "idle"
            return
        if p.stuffing:
            if p.run == 5:  # a stuff bit
                if lvl == p.last:
                    self._rx_error("stuff")
                    return
                p.run, p.last = 1, lvl
                if p.need is not None and len(p.bits) == p.need:
                    p.stuffing = False
                return
            p.run, p.last = (p.run + 1, lvl) if lvl == p.last else (1, lvl)
            p.bits.append(lvl)
            n = len(p.bits)
            if n == 14:
                p.hdr = 39 if p.bits[13] else 19
            if p.hdr and n == p.hdr:
                rtr = p.bits[32 if p.hdr == 39 else 12]
                dlc = _num(p.bits[p.hdr - 4 : p.hdr])
                p.need = p.hdr + (0 if rtr else 8 * min(dlc, 8)) + 15
            if p.need is not None and n == p.need:
                p.crc_ok = _crc15(p.bits[:-15]) == _num(p.bits[-15:])
                if p.run < 5:
                    p.stuffing = False
            return
        p.tail += 1
        t = p.tail
        receiver = self.tx is None
        if t == 1:  # CRC delimiter
            if lvl == 0:
                self._rx_error("form")
            elif receiver and p.crc_ok and self.ack:
                self._ack_next = True
        elif t == 3:  # ACK delimiter
            if not p.crc_ok:
                self._rx_error("crc")
            elif lvl == 0:
                self._rx_error("form")
        elif 4 <= t <= 9 and lvl == 0:  # EOF 1..6 (EOF 7 is not checked by receivers)
            self._rx_error("form")
        elif t == 9 and receiver:
            self._accept()
        elif t == 10 and receiver:
            self.mode, self._n = "intermission", 0

    def _fields(self, bits: list[int]) -> dict[str, Any]:
        ext = bits[13] == 1
        hdr = 39 if ext else 19
        ident = (_num(bits[1:12]) << 18) | _num(bits[14:32]) if ext else _num(bits[1:12])
        rtr = bits[32 if ext else 12] == 1
        dlc = _num(bits[hdr - 4 : hdr])
        nbytes = 0 if rtr else min(dlc, 8)
        data = [_num(bits[hdr + 8 * k : hdr + 8 * k + 8]) for k in range(nbytes)]
        return {"id": ident, "ext": ext, "rtr": rtr, "dlc": dlc, "data": data}

    def _accept(self) -> None:
        p = self._rx
        assert p is not None
        self.emit("rx", start=p.start, **self._fields(p.bits))
        if self.rec > 127:
            self.rec = 120
        elif self.rec > 0:
            self.rec -= 1
        self._update_state()

    def _tx_done(self) -> None:
        tx = self.tx
        assert tx is not None
        f = tx.send.frame
        self.emit("sent", id=f.id, ext=f.ext, rtr=f.rtr, dlc=f.length, data=list(f.payload),
                  attempt=tx.attempt)
        self.tec = max(0, self.tec - 1)
        self._update_state()
        self._next_frame()
        self._suspend_pending = self.state == "error_passive"
        self.tx = None
        self.mode, self._n = "intermission", 0

    def _next_frame(self) -> None:
        self.queue.pop(0)
        self._attempt = self._fails = 0
        if self.queue:
            self._not_before = self.cycle + round(self.queue[0].gap_bits * self._bit_cycles)

    def _rx_error(self, kind: str) -> None:
        if self.tx is not None:  # errors in our own frame show up as bit errors
            assert self._rx is not None
            self._rx.dead = True
            return
        self._error(kind)

    def _error(self, kind: str) -> None:
        p = self._rx
        bit = len(p.bits) if p is not None else 0
        tx = self.tx
        self.emit("error", type=kind, bit=bit, role="tx" if tx is not None else "rx")
        self._ack_next = False
        if tx is not None:
            if kind == "ack" and self.state == "error_passive":
                self._ack_exception = True
            else:
                self.tec += 8
            self._suspend_pending = self.state == "error_passive"
            self.tx = None
            self._fails += 1
            if self.retransmit is not None and self._fails > self.retransmit:
                self.emit("tx_abort", id=tx.send.frame.id, attempts=self._attempt,
                          tec=self.tec, rec=self.rec)
                self._next_frame()
        else:
            self.rec += 1
        if self._update_state():
            return
        self._flag_active = self.state == "error_active"
        self.emit("error_flag", passive=not self._flag_active)
        self.mode, self._n = "eflag", 0

    def _update_state(self) -> bool:
        """Recompute the fault-confinement state; True if the node just went bus-off."""
        new = ("bus_off" if self.tec > 255 else
               "error_passive" if self.tec >= 128 or self.rec >= 128 else "error_active")
        if new == self.state:
            return False
        self.state = new
        self.emit("state", state=new, tec=self.tec, rec=self.rec)
        if new == "bus_off":
            self.tx = None
            self.mode, self._n, self._seq = "bus_off", 0, 0
            self._drv = 1
            return True
        return False

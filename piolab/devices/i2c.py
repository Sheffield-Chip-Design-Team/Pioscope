"""I2C slave and master models. Both only ever drive 0 or release (open-drain).

Slave logged transactions:
    start / restart / stop
    addr   {addr, rw, ack}
    write  {byte, ack}            byte written by the master
    read   {byte, master_ack}     byte sent to the master
Master logged transactions:
    start / restart / stop, addr {addr, rw, ack}, write {byte, ack}, read {byte},
    arb_lost {phase, bit}, done
"""

from __future__ import annotations

from collections.abc import Generator, Iterable, Sequence
from dataclasses import dataclass

from piolab.wave import Z

from .base import At, Device, Script, ScriptedDevice, Wait

# ------------------------------------------------------------------------------
# Slave
# ------------------------------------------------------------------------------


class I2cSlave(Device):
    ports = ("scl", "sda")

    def __init__(
        self,
        name: str,
        address: int,
        read_data: Iterable[int] = (),
        nack_addr: bool = False,
        nack_after: int | None = None,  # NACK the write byte with this index (0-based)
        stretch_cycles: int = 0,  # hold SCL low this long after each ACK clock
        hold_cycles: int = 5,  # SDA change delay after SCL falls (tHD;DAT)
        seed: int = 0,
    ) -> None:
        super().__init__(name, seed)
        self.address = address
        self.read_data = list(read_data)
        self.nack_addr, self.nack_after = nack_addr, nack_after
        self.stretch_cycles, self.hold = stretch_cycles, hold_cycles
        self.state = "idle"
        self._prev_scl = self._prev_sda = 1
        self._pending: tuple[int, int] | None = None  # (cycle, drive)
        self._stretch_until: int | None = None
        self._n = 0
        self._shift = 0
        self._rw = 0
        self._writes = 0
        self._ack_this = True
        self._tx = 0
        self._master_ack = False

    def _sda_later(self, value: int) -> None:
        self._pending = (self.cycle + self.hold, Z if value else 0)

    def _stretch(self) -> None:
        if self.stretch_cycles:
            self.drive("scl", 0)
            self._stretch_until = self.cycle + self.stretch_cycles
            self.emit("stretch", cycles=self.stretch_cycles)

    def _present(self) -> None:
        self._sda_later((self._tx >> (7 - self._n)) & 1)

    def tick(self) -> None:
        if self._pending is not None and self.cycle >= self._pending[0]:
            self.drive("sda", self._pending[1])
            self._pending = None
        if self._stretch_until is not None and self.cycle >= self._stretch_until:
            self.release("scl")
            self._stretch_until = None
        scl, sda = self.read("scl"), self.read("sda")
        if scl not in (0, 1) or sda not in (0, 1):
            return
        ps, pd = self._prev_scl, self._prev_sda
        self._prev_scl, self._prev_sda = scl, sda
        if ps == 1 and scl == 1 and pd != sda:
            if sda == 0:
                self.emit("restart" if self.state != "idle" else "start")
                self.state, self._n, self._shift = "addr", 0, 0
            else:
                self.emit("stop")
                self.state = "idle"
            self._pending = None
            self.release("sda")
            return
        if ps == 0 and scl == 1:
            self._rising(sda)
        elif ps == 1 and scl == 0:
            self._falling()

    def _rising(self, sda: int) -> None:
        st = self.state
        if st in ("addr", "write"):
            self._shift = (self._shift << 1) | sda
            self._n += 1
        elif st == "read":
            self._n += 1
        elif st == "read_ack":
            self._master_ack = sda == 0
            self.emit("read", byte=self._tx, master_ack=self._master_ack)

    def _falling(self) -> None:
        st = self.state
        if st == "addr" and self._n == 8:
            addr, self._rw = self._shift >> 1, self._shift & 1
            ack = addr == self.address and not self.nack_addr
            if addr == self.address:
                self.emit("addr", addr=addr, rw=self._rw, ack=ack)
            if ack:
                self._sda_later(0)
                self.state = "addr_ack"
            else:
                self.state = "wait_stop"
        elif st == "write" and self._n == 8:
            self._ack_this = self.nack_after is None or self._writes != self.nack_after
            self.emit("write", byte=self._shift, ack=self._ack_this)
            self._writes += 1
            if self._ack_this:
                self._sda_later(0)
            self.state = "write_ack"
        elif st == "addr_ack":
            self._sda_later(1)
            self._stretch()
            self._n, self._shift = 0, 0
            if self._rw:
                self._load_and_present()
            else:
                self.state = "write"
        elif st == "write_ack":
            self._sda_later(1)
            self._stretch()
            self._n, self._shift = 0, 0
            self.state = "write" if self._ack_this else "wait_stop"
        elif st == "read":
            if self._n < 8:
                self._present()
            else:
                self._sda_later(1)
                self.state = "read_ack"
        elif st == "read_ack":
            if self._master_ack:
                self._load_and_present()
            else:
                self.state = "wait_stop"

    def _load_and_present(self) -> None:
        self._tx = self.read_data.pop(0) if self.read_data else 0xFF
        self._n = 0
        self.state = "read"
        self._present()


# ------------------------------------------------------------------------------
# Master
# ------------------------------------------------------------------------------


@dataclass(frozen=True)
class I2cOp:
    kind: str  # write | read
    addr: int
    data: Sequence[int] = ()  # bytes to write
    count: int = 0  # bytes to read
    stop: bool = True  # False -> the next op starts with a repeated START


class _Lost(Exception):
    pass


class I2cMaster(ScriptedDevice):
    ports = ("scl", "sda")

    def __init__(
        self,
        name: str,
        ops: Sequence[I2cOp],
        half_period: int = 250,  # 100 kHz at 50 MHz
        start_delay: int = 20,
        bus_free_cycles: int = 50,
        scl_hz: float | None = None,
        ppm: float = 0.0,
        jitter_rms_s: float = 0.0,
        seed: int = 0,
    ) -> None:
        """SCL timing: `half_period` whole sysclk cycles, or, with `scl_hz`, the
        master's own asynchronous timebase (ppm, seeded per-edge jitter). Timing is
        relative to the last action, since stretching and clock sync move the clock."""
        super().__init__(name, seed)
        self.ops = list(ops)
        self.half = half_period
        self.hold = max(1, half_period // 4)
        self.start_delay = start_delay
        self.bus_free = bus_free_cycles
        self.scl_hz = scl_hz
        self.half_s = 1 / (2 * scl_hz * (1 + ppm * 1e-6)) if scl_hz else 0.0
        self.jitter_rms_s = jitter_rms_s

    def _w(self, cycles: int, seconds: float) -> Wait:
        """A delay: whole cycles, or (with scl_hz) `seconds` on the own timebase."""
        if not self.scl_hz:
            return cycles
        j = self.rng.gauss(0.0, self.jitter_rms_s) if self.jitter_rms_s else 0.0
        return At(self.now() + max(seconds + j, seconds / 2))

    def _high(self) -> Generator[int | object, None, None]:
        self.release("scl")
        yield lambda: self.read("scl") == 1  # clock stretching / synchronisation

    def _bit(self, b: int, arbitrate: bool, phase: str, idx: int) -> Generator[object, None, int]:
        yield self._w(self.hold, self.half_s / 4)
        if b:
            self.release("sda")
        else:
            self.drive("sda", 0)
        yield self._w(self.half - self.hold, self.half_s * 3 / 4)
        yield from self._high()
        yield self._w(max(1, self.half // 2), self.half_s / 2)
        s = self.read("sda")
        if arbitrate and b == 1 and s == 0:
            self.release("sda")
            self.release("scl")
            self.emit("arb_lost", phase=phase, bit=idx)
            raise _Lost
        yield self._w(max(1, self.half - self.half // 2), self.half_s / 2)
        self.drive("scl", 0)
        return int(s == 1)

    def _byte_out(self, byte: int, phase: str) -> Generator[object, None, bool]:
        for i in range(8):
            yield from self._bit((byte >> (7 - i)) & 1, True, phase, i)
        nack = yield from self._bit(1, False, phase, 8)
        return not nack

    def _byte_in(self, ack: bool) -> Generator[object, None, int]:
        v = 0
        for i in range(8):
            v = (v << 1) | (yield from self._bit(1, False, "read", i))
        yield from self._bit(0 if ack else 1, False, "read_ack", 8)
        return v

    def _start(self) -> Generator[object, None, None]:
        self.drive("sda", 0)
        self.emit("start")
        yield self._w(self.half, self.half_s)
        self.drive("scl", 0)

    def _restart(self) -> Generator[object, None, None]:
        yield self._w(self.hold, self.half_s / 4)
        self.release("sda")
        yield self._w(self.half - self.hold, self.half_s * 3 / 4)
        yield from self._high()
        yield self._w(self.half, self.half_s)
        self.drive("sda", 0)
        self.emit("restart")
        yield self._w(self.half, self.half_s)
        self.drive("scl", 0)

    def _stop(self) -> Generator[object, None, None]:
        yield self._w(self.hold, self.half_s / 4)
        self.drive("sda", 0)
        yield self._w(self.half - self.hold, self.half_s * 3 / 4)
        yield from self._high()
        yield self._w(self.half, self.half_s)
        self.release("sda")
        self.emit("stop")
        yield self._w(self.half, self.half_s)

    def script(self) -> Script:
        yield self.start_delay
        free = 0
        while free < self.bus_free:
            yield 1
            free = free + 1 if self.read("scl") == 1 and self.read("sda") == 1 else 0
        try:
            in_txn = False
            for op in self.ops:
                if in_txn:
                    yield from self._restart()  # type: ignore[misc]
                else:
                    yield from self._start()  # type: ignore[misc]
                rw = 1 if op.kind == "read" else 0
                ack = yield from self._byte_out((op.addr << 1) | rw, "addr")  # type: ignore[misc]
                self.emit("addr", addr=op.addr, rw=rw, ack=ack)
                if ack and op.kind == "write":
                    for b in op.data:
                        ok = yield from self._byte_out(b, "write")  # type: ignore[misc]
                        self.emit("write", byte=b, ack=ok)
                        if not ok:
                            break
                elif ack:
                    for i in range(op.count):
                        v = yield from self._byte_in(i < op.count - 1)  # type: ignore[misc]
                        self.emit("read", byte=v)
                if op.stop or not ack:
                    yield from self._stop()  # type: ignore[misc]
                    in_txn = False
                else:
                    in_txn = True
            self.emit("done")
        except _Lost:
            return

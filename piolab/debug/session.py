"""The debugger core shared by the terminal (repl.py) and VS Code (dap.py) front ends.

A `DebugSession` drives the same `PySession` as `piolab sim` with the engine in the DUT
slot, so what you step through is exactly the run the bench executes.

It follows the model rather than knowing it (ADR-0010). It needs only:
- `dut.cores`: a list of cores, each with `name`, `prog`, `labels`, `image`,
  `issued`, `cur`, `issue_pc()`, `set_reg()` and `state()`;
- `dut.state()`: {"cycle", "time_s", "cores": {name: state}, "shared": {...}}.
Registers, flags, units, pins, extra cores and shared units are found by walking
that data, so a model that adds them needs no change here.

Stepping (GDB vocabulary), on the *selected* core; the others run in the same
cycles (the engine is one clock domain):
  stepi  one sysclk cycle
  step   until the next instruction is about to issue (runs through a whole
         multi-cycle instruction: "step over")
  finish until the current instruction completes
  cont   until a breakpoint, watchpoint, catchpoint, the end, or a cycle limit
A breakpoint stops *before* the instruction at its address issues, as in GDB. A
breakpoint without a core prefix applies to every core whose program has that
location; `t1:loop` applies to t1 only. Stopping at a breakpoint selects the core
that hit it, as GDB switches to the thread that stopped.
"""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from piolab.bench.backends.python import PySession
from piolab.bench.scenario import Result, Scenario
from piolab.devices.base import Txn
from piolab.dut.core import EngineError, short_src

ROOT_KEYS = ("cycle", "time_s", "cores", "shared")


@dataclass
class Breakpoint:
    id: int
    addrs: dict[int, int]  # core index -> address
    where: str  # what the user typed
    enabled: bool = True
    temporary: bool = False
    hits: int = 0

    @property
    def addr(self) -> int:
        return next(iter(self.addrs.values()))


@dataclass
class Watch:
    id: int
    path: str
    last: Any = None


@dataclass
class Catch:
    id: int
    source: str
    kind: str | None


@dataclass
class Stop:
    reason: str  # entry | step | breakpoint | watch | catch | finished | limit | pause | error
    cycle: int
    detail: dict[str, Any] = field(default_factory=dict)


class DebugError(Exception):
    pass


class DebugSession:
    def __init__(self, factory: Callable[[], Scenario], history: int = 5000,
                 dut: Callable[[], Any] | str | None = None, model: str | None = None) -> None:
        """`factory` builds the scenario; `dut` builds the model to debug: a factory, or a
        `--dut` name (default `engine`: engine model `model` of the program's ISA pack,
        None for the project's or pack's default)."""
        self.factory = factory
        self.dut_spec: Callable[[], Any] | str = dut or "engine"
        self.model = model
        self.breakpoints: dict[int, Breakpoint] = {}
        self.watches: dict[int, Watch] = {}
        self.catches: dict[int, Catch] = {}
        self._next_id = 1
        self.history: deque[tuple[str, Txn]] = deque(maxlen=history)
        self.on_event: Callable[[str, Txn], None] | None = None
        self.pause_requested = False
        self.vcd_path: Path | None = None  # rewritten with the waveform so far at every stop
        self.selected = 0
        self.restart()

    # ---- lifecycle ----------------------------------------------------------------------
    def restart(self) -> Stop:
        sc = self.factory()
        if sc.image is None:
            raise DebugError(f"scenario '{sc.name}' has no program to debug")
        self.engine: Any  # a model with the observability contract (module doc)
        if isinstance(self.dut_spec, str):
            from piolab.dut import dut_factory

            self.engine = dut_factory(self.dut_spec, sc.image, self.model)()
        else:
            self.engine = self.dut_spec()
        self.session = PySession(sc, self.engine)
        if not getattr(self.engine, "cores", None):
            raise DebugError(f"the DUT {type(self.engine).__name__} has no cores to debug "
                             "(see docs/reference/simulator.md, observability contract)")
        self.selected = min(self.selected, len(self.cores) - 1)
        self.history.clear()
        for w in self.watches.values():
            w.last = self._try_eval(w.path)
        return self._stop(Stop("entry", 0))

    @property
    def scenario(self) -> Scenario:
        return self.session.scenario

    @property
    def finished(self) -> bool:
        return self.session.finished

    @property
    def cycle(self) -> int:
        return self.session.cycle

    def result(self) -> Result:
        return self.session.result()

    # ---- cores ----------------------------------------------------------------------
    @property
    def cores(self) -> list[Any]:
        return list(self.engine.cores)

    @property
    def names(self) -> list[str]:
        return [t.name for t in self.cores]

    @property
    def core(self) -> Any:
        """The selected core."""
        return self.cores[self.selected]

    def index_of(self, which: str | int) -> int:
        """A core index from a name (`t1`) or a number (`1`)."""
        if isinstance(which, int) or str(which).isdigit():
            i = int(which)
            if 0 <= i < len(self.cores):
                return i
        elif which in self.names:
            return self.names.index(str(which))
        raise DebugError(f"no core '{which}' (cores: {', '.join(self.names)})")

    def select(self, which: str | int) -> int:
        self.selected = self.index_of(which)
        return self.selected

    def _t(self, ti: int | None) -> Any:
        return self.cores[self.selected if ti is None else ti]

    def _split(self, where: str) -> tuple[int | None, str]:
        """`t1:loop` -> (1, "loop"); `loop` -> (None, "loop")."""
        head, sep, rest = where.strip().partition(":")
        if sep and head in self.names:
            return self.names.index(head), rest
        return None, where.strip()

    # ---- locations ----------------------------------------------------------------------
    @property
    def program(self) -> list[Any]:
        """The selected core's program."""
        return list(self.core.prog)

    def resolve(self, where: str, ti: int | None = None) -> int:
        """Address of a label, `*addr` / number, `file:line` or `line` in a core's
        program (default: the selected one, or the one named by a `tN:` prefix)."""
        t_pre, w = self._split(where)
        t = self._t(t_pre if t_pre is not None else ti)
        symbols = t.image.symbols
        if w in symbols:
            return int(symbols[w])
        if w.startswith("*"):
            return self._addr(t, int(w[1:], 0))
        file, _, line = w.rpartition(":")
        if line.isdigit():
            return self._addr_of_line(t, int(line), file or None)
        raise DebugError(f"no label, address or line '{where}' (labels: "
                         f"{', '.join(sorted(symbols)) or 'none'})")

    @staticmethod
    def _addr(t: Any, a: int) -> int:
        if not 0 <= a < len(t.prog):
            raise DebugError(f"address {a} is outside {t.name}'s program "
                             f"(0..{len(t.prog) - 1})")
        return a

    @staticmethod
    def _addr_of_line(t: Any, line: int, file: str | None) -> int:
        """The first instruction at or after `line` (as GDB moves a breakpoint to code)."""
        best: tuple[int, int] | None = None
        for ins in t.prog:
            f, _, ln = ins.src.rpartition(":")
            if file and not (f.endswith(file) or os.path.basename(f) == os.path.basename(file)):
                continue
            n = int(ln)
            if n >= line and (best is None or n < best[0]):
                best = (n, ins.addr)
        if best is None:
            raise DebugError(f"no instruction at or after line {line} in {t.name}'s program")
        return best[1]

    def line_of(self, addr: int, ti: int | None = None) -> tuple[str, int]:
        f, _, ln = self._t(ti).prog[addr].src.rpartition(":")
        return f, int(ln)

    # ---- breakpoints, watches, catches -------------------------------------------------
    def _id(self) -> int:
        i, self._next_id = self._next_id, self._next_id + 1
        return i

    def add_breakpoint(self, where: str, temporary: bool = False) -> Breakpoint:
        ti, _ = self._split(where)
        targets = [ti] if ti is not None else range(len(self.cores))
        addrs: dict[int, int] = {}
        error: DebugError | None = None
        for i in targets:
            try:
                addrs[i] = self.resolve(where, i)
            except DebugError as e:
                error = error or e
        if not addrs:
            assert error is not None
            raise error
        bp = Breakpoint(self._id(), addrs, where, temporary=temporary)
        self.breakpoints[bp.id] = bp
        return bp

    def set_line_breakpoints(self, file: str, lines: list[int]) -> list[Breakpoint | None]:
        """Replace the breakpoints in `file` (DAP setBreakpoints): one per line, or None."""
        for i in [i for i, b in self.breakpoints.items() if ":" in b.where
                  and os.path.basename(b.where.rpartition(":")[0]) == os.path.basename(file)]:
            del self.breakpoints[i]
        out: list[Breakpoint | None] = []
        for ln in lines:
            try:
                out.append(self.add_breakpoint(f"{file}:{ln}"))
            except DebugError:
                out.append(None)
        return out

    def set_instruction_breakpoints(self, refs: list[tuple[int, int]]
                                    ) -> list[Breakpoint | None]:
        """Replace the address breakpoints (DAP setInstructionBreakpoints); each ref is
        (core index, address)."""
        for i in [i for i, b in self.breakpoints.items() if b.where.partition(":")[2]
                  .startswith("*") or b.where.startswith("*")]:
            del self.breakpoints[i]
        out: list[Breakpoint | None] = []
        for ti, a in refs:
            try:
                out.append(self.add_breakpoint(f"{self.names[ti]}:*{a}"))
            except (DebugError, IndexError):
                out.append(None)
        return out

    def disassemble(self, start: int, count: int, ti: int | None = None
                    ) -> list[dict[str, Any] | None]:
        """`count` rows of a core's program from address `start` (may be negative or
        past the end: None there). Each row: addr, text, word (the binary encoding, or
        None), word_bits (the ISA's word width), label, file, line."""
        from piolab.isa import load_isa
        from piolab.packs import for_image

        t = self._t(ti)
        words = t.image.words
        word_bits = load_isa(for_image(t.image).spec).encoding.word_bits
        rows: list[dict[str, Any] | None] = []
        for a in range(start, start + count):
            if not 0 <= a < len(t.prog):
                rows.append(None)
                continue
            f, _, ln = t.prog[a].src.rpartition(":")
            rows.append({"addr": a, "text": t.prog[a].text, "label": t.labels.get(a),
                         "word": words[a] if a < len(words) else None,
                         "word_bits": word_bits, "file": f,
                         "line": int(ln)})
        return rows

    def delete(self, ident: int | None = None) -> None:
        if ident is None:
            self.breakpoints.clear()
            self.watches.clear()
            self.catches.clear()
            return
        for table in (self.breakpoints, self.watches, self.catches):
            if ident in table:
                del table[ident]
                return
        raise DebugError(f"no breakpoint, watchpoint or catchpoint {ident}")

    def enable(self, ident: int, on: bool = True) -> None:
        if ident not in self.breakpoints:
            raise DebugError(f"no breakpoint {ident}")
        self.breakpoints[ident].enabled = on

    def add_watch(self, path: str) -> Watch:
        w = Watch(self._id(), path, self.eval(path))
        self.watches[w.id] = w
        return w

    def add_catch(self, spec: str) -> Catch:
        source, _, kind = spec.partition(":")
        c = Catch(self._id(), source, kind or None)
        self.catches[c.id] = c
        return c

    # ---- state ------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        """The whole model: cycle, time_s, cores, shared, finished."""
        s: dict[str, Any] = self.engine.state()
        s["finished"] = self.finished
        return s

    def core_state(self, ti: int | None = None) -> dict[str, Any]:
        st: dict[str, Any] = self._t(ti).state()
        return st

    def eval(self, path: str, ti: int | None = None) -> Any:
        """A value from the state. Resolution, in order:
        - `$cycle`, `$time`;
        - a path from the top: `cycle`, `shared.arb.grant`, `cores.sm1.regs.x`;
        - a path from a core by name: `sm1.regs.x`, `sm0.units.<unit>.<field>`;
        - a path from the selected core (or `ti`): `regs.x`, `fifo.tx.level`;
        - a bare name found exactly once in that core: `x`, `isr`, `pc`."""
        p = path.strip()
        s = self.engine.state()
        if p in ("$cycle", "cycle"):
            return s["cycle"]
        if p in ("$time", "time"):
            return s["time_s"]
        parts = p.split(".")
        if parts[0] in ROOT_KEYS:
            return self._walk(s, parts, p)
        name = self._t(ti).name
        if parts[0] in s["cores"] and len(parts) > 1:
            name, parts = parts[0], parts[1:]
        ts = s["cores"][name]
        try:
            return self._walk(ts, parts, p)
        except DebugError:
            if len(parts) != 1:
                raise
        hits = list(self._find(ts, parts[0]))
        if len(hits) == 1:
            return hits[0][1]
        if hits:
            raise DebugError(f"'{p}' is ambiguous: " + ", ".join(h for h, _ in hits))
        raise DebugError(f"no '{p}' in {name}'s state (try: x, isr, pc, "
                         "regs, flags, fifo.tx, units, pins.in, "
                         f"{'/'.join(self.names)}.<path>, shared)")

    @staticmethod
    def _walk(node: Any, parts: list[str], whole: str) -> Any:
        for part in parts:
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                raise DebugError(f"no '{whole}' in the engine state")
        return node

    @classmethod
    def _find(cls, node: Any, key: str, prefix: str = "") -> list[tuple[str, Any]]:
        out: list[tuple[str, Any]] = []
        if isinstance(node, dict):
            for k, v in node.items():
                here = f"{prefix}.{k}" if prefix else str(k)
                if k == key:
                    out.append((here, v))
                out += cls._find(v, key, here)
        return out

    def _try_eval(self, path: str) -> Any:
        try:
            return self.eval(path)
        except DebugError:
            return None

    def set_var(self, path: str, value: int, ti: int | None = None) -> None:
        """Set x, y, osr, isr or pc of a core: `x`, `regs.x`, `t1.x`, `t1.regs.pc`."""
        p = path.strip()
        if p.startswith("cores."):
            p = p.removeprefix("cores.")
        head, sep, rest = p.partition(".")
        if sep and head in self.names:
            ti, p = self.names.index(head), rest
        p = p.removeprefix("regs.")
        try:
            self._t(ti).set_reg(p, value)
        except EngineError as e:
            raise DebugError(str(e)) from e
        for w in self.watches.values():
            w.last = self._try_eval(w.path)

    # ---- running ------------------------------------------------------------------------
    def _cycle(self) -> list[tuple[str, Txn]]:
        events = self.session.step()
        for e in events:
            self.history.append(e)
            if self.on_event is not None:
                self.on_event(*e)
        return events

    def _check(self, events: list[tuple[str, Txn]]) -> Stop | None:
        for c in self.catches.values():
            for src, t in events:
                if src == c.source and (c.kind is None or t.kind == c.kind):
                    if src in self.names:
                        self.selected = self.names.index(src)
                    return Stop("catch", self.cycle, {"id": c.id, "source": src, "event": t})
        for w in self.watches.values():
            v = self._try_eval(w.path)
            if v != w.last:
                old, w.last = w.last, v
                return Stop("watch", self.cycle, {"id": w.id, "path": w.path, "old": old,
                                                  "new": v})
        return None

    def _at_breakpoint(self) -> Stop | None:
        for i, t in enumerate(self.cores):
            pc = t.issue_pc()
            if pc is None:
                continue
            for bp in list(self.breakpoints.values()):
                if bp.enabled and bp.addrs.get(i) == pc:
                    bp.hits += 1
                    if bp.temporary:
                        del self.breakpoints[bp.id]
                    self.selected = i
                    return Stop("breakpoint", self.cycle, {"id": bp.id, "addr": pc,
                                                           "temporary": bp.temporary})
        return None

    def _run(self, until: Callable[[int], bool], max_cycles: int | None,
             breakpoints: bool = True) -> Stop:
        """Run cycles; `until(n_done)` is checked before each new cycle. A breakpoint at
        the current PC is honoured only from the entry point (not when resuming from it)."""
        n = 0
        check_now = breakpoints and self.last_stop.reason == "entry"
        self.pause_requested = False
        try:
            while True:
                if self.finished:
                    return self._stop(Stop("finished", self.cycle))
                if n and until(n):
                    return self._stop(Stop("step", self.cycle))
                if breakpoints and (n or check_now):
                    hit = self._at_breakpoint()
                    if hit:
                        return self._stop(hit)
                if max_cycles is not None and n >= max_cycles:
                    return self._stop(Stop("limit", self.cycle, {"cycles": n}))
                if self.pause_requested:
                    return self._stop(Stop("pause", self.cycle))
                events = self._cycle()
                n += 1
                hit2 = self._check(events)
                if hit2:
                    return self._stop(hit2)
        except EngineError as e:
            return self._stop(Stop("error", self.cycle, {"error": str(e)}))

    def _stop(self, s: Stop) -> Stop:
        s.detail.setdefault("core", self.selected)
        self.last_stop = s
        if self.vcd_path is not None:
            self.write_vcd(self.vcd_path)
        return s

    def write_vcd(self, path: Path | str) -> None:
        """Write the waveform recorded so far (bench nets and engine probes) as VCD,
        exactly what `piolab sim --vcd` writes, cut at the current cycle, plus a GTKWave
        save file next to it (same name, `.gtkw`) with the signals grouped."""
        wave = self.session.board.wave
        wave.to_vcd(path)
        wave.to_gtkw(Path(path).with_suffix(".gtkw"), path)

    def set_vcd(self, path: Path | str | None) -> None:
        """Keep `path` up to date at every stop (None turns it off); writes it now."""
        self.vcd_path = Path(path) if path is not None else None
        if self.vcd_path is not None:
            self.vcd_path.parent.mkdir(parents=True, exist_ok=True)
            self.write_vcd(self.vcd_path)

    def stepi(self, n: int = 1) -> Stop:
        return self._run(lambda done: done >= n, None, breakpoints=False)

    def step(self, n: int = 1, max_cycles: int | None = 500_000) -> Stop:
        """Run until the selected core has issued `n` more instructions and the next
        is about to issue."""
        t = self.core
        # an instruction already in progress counts as the first of the n
        target = t.issued + n - (1 if t.cur is not None else 0)

        def done(_: int) -> bool:
            return bool(t.issued >= target and t.issue_pc() is not None)

        return self._run(done, max_cycles, breakpoints=False)

    def finish(self, max_cycles: int | None = 500_000) -> Stop:
        t = self.core
        if t.cur is None:
            return self.step(1, max_cycles)
        return self._run(lambda _: t.cur is None, max_cycles, breakpoints=False)

    def cont(self, max_cycles: int | None = None) -> Stop:
        return self._run(lambda _: False, max_cycles)

    def until(self, where: str) -> Stop:
        self.add_breakpoint(where, temporary=True)
        return self.cont()

    # ---- presentation helpers ----------------------------------------------------------
    def source_lines(self, file: str | None = None) -> tuple[str, list[str]]:
        f = file or self.line_of(self.core.pc)[0]
        try:
            return f, Path(f).read_text().splitlines()
        except OSError as e:
            raise DebugError(f"cannot read {f}: {e}") from e

    def where(self, ti: int | None = None) -> dict[str, Any]:
        t = self._t(ti)
        s = t.state()
        f, ln = self.line_of(s["pc"], self.cores.index(t))
        return {"core": t.name, "pc": s["pc"], "label": s["label"], "file": f,
                "short": short_src(f"{f}:{ln}"), "line": ln, "instr": s["instr"],
                "cycle": self.cycle, "in_progress": s["in_progress"], "stall": s["stall"]}

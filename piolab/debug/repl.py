"""`piolab debug`: a GDB-style terminal debugger for programs on their ISA pack's model.

    uv run piolab debug --isa pio uart_rx --device uart:baud=115200,send=0x55
    (piolab) break bitloop
    (piolab) continue
    (piolab) step
    (piolab) p/x isr
    (piolab) info shared
    (piolab) info cores          # every core of the model; `core sm1` selects one

`help` lists the commands; an empty line repeats the last one, as in GDB. Nothing here
names a register, unit or core: `info`, `print` and the stop reports read whatever the
model's state() contains (ADR-0010).
"""

from __future__ import annotations

import cmd
import json
import os
from collections.abc import Sequence
from typing import IO, Any

from piolab.bench.report import format_event
from piolab.devices.base import Txn

from .session import DebugError, DebugSession, Stop


def fmt(v: Any, f: str = "") -> str:
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, int):
        if f == "x":
            return hex(v)
        if f == "t":
            return bin(v)
        if f == "d":
            return str(v)
        return str(v) if -10 < v < 10 else f"{v} ({hex(v)})"
    if isinstance(v, (dict, list)):
        return json.dumps(v, indent=2, default=str)
    return str(v)


class Repl(cmd.Cmd):
    prompt = "(piolab) "
    intro = ("PIOLab debugger (engine model v0). `help` for commands; "
             "`break <label|line>`, then `continue`.")

    def __init__(self, session: DebugSession, stdout: IO[str] | None = None) -> None:
        super().__init__(stdout=stdout)
        self.s = session
        self.displays: dict[int, tuple[str, str]] = {}
        self._next_display = 1
        self.trace = False
        self.s.on_event = self._on_event
        self.quit = False

    # ---- output -----------------------------------------------------------------------
    def out(self, text: str = "") -> None:
        self.stdout.write(text + "\n")

    def _on_event(self, src: str, t: Txn) -> None:
        if self.trace:
            self.out(format_event(src, t))

    def onecmd(self, line: str) -> bool:
        try:
            return super().onecmd(line)
        except DebugError as e:
            self.out(f"error: {e}")
            return False
        except (ValueError, KeyError) as e:
            self.out(f"error: {e}")
            return False

    def default(self, line: str) -> None:
        self.out(f'Undefined command: "{line.split()[0]}".  Try "help".')

    def emptyline(self) -> bool:
        return self.onecmd(self.lastcmd) if self.lastcmd else False

    def _source_line(self, file: str, line: int, mark: str = "") -> str:
        try:
            text = self.s.source_lines(file)[1][line - 1]
        except (DebugError, IndexError):
            text = ""
        return f"{mark:2s}{line:<5d} {text}"

    def show_where(self) -> None:
        w = self.s.where()
        self.out(self._source_line(w["file"], w["line"]))

    def _who(self, name: str | None = None) -> str:
        """`[t1] ` when the model has more than one core, else nothing."""
        return f"[{name or self.s.core.name}] " if len(self.s.cores) > 1 else ""

    def report(self, st: Stop) -> None:
        w = self.s.where()
        loc = self._who() + (f"{w['label']} () at {w['short']}" if w["label"]
                             else f"pc={w['pc']} at {w['short']}")
        d = st.detail
        if st.reason == "breakpoint":
            kind = "Temporary breakpoint" if d.get("temporary") else "Breakpoint"
            self.out(f"\n{kind} {d['id']}, {loc}")
        elif st.reason == "watch":
            self.out(f"\nWatchpoint {d['id']}: {d['path']}\n\nOld value = {fmt(d['old'])}\n"
                     f"New value = {fmt(d['new'])}")
            self.out(f"[cycle {st.cycle}] {loc}")
        elif st.reason == "catch":
            t: Txn = d["event"]
            self.out(f"\nCatchpoint {d['id']} (event {d['source']}:{t.kind}), "
                     f"{format_event(d['source'], t).strip()}")
            self.out(f"[cycle {st.cycle}] {loc}")
        elif st.reason == "finished":
            r = self.s.result()
            self.out(f"[Scenario '{r.scenario}' finished after {r.cycles} cycles]")
            for tc, words in r.host_rx.items():
                if words:
                    self.out(f"  host rx t{tc}: {[hex(x) for x in words]}")
            return
        elif st.reason == "error":
            self.out(f"\nEngine error at cycle {st.cycle}: {d['error']}")
        elif st.reason == "limit":
            self.out(f"[stopped after {d['cycles']} cycles without reaching the target] {loc}")
        else:
            state = f" (in progress: {w['stall'] or 'running'})" if w["in_progress"] else ""
            self.out(f"[cycle {st.cycle}] {loc}{state}")
        self.show_where()
        for i, (expr, f) in self.displays.items():
            self.out(f"{i}: {expr} = {fmt(self.s.eval(expr), f)}")

    # ---- running ----------------------------------------------------------------------
    @staticmethod
    def _count(arg: str) -> int:
        return int(arg, 0) if arg.strip() else 1

    def do_run(self, arg: str) -> None:
        """run: restart the scenario from cycle 0 and continue."""
        self.s.restart()
        self.report(self.s.cont())

    def do_start(self, arg: str) -> None:
        """start: restart the scenario and stop before the first instruction."""
        self.s.restart()
        self.out("[restarted at cycle 0]")
        self.show_where()

    def do_continue(self, arg: str) -> None:
        """continue [CYCLES]: run until a breakpoint, watchpoint, catchpoint or the end."""
        self.report(self.s.cont(int(arg, 0) if arg.strip() else None))

    do_c = do_continue

    def do_step(self, arg: str) -> None:
        """step [N]: run until N more instructions have executed (a multi-cycle
        instruction runs to completion) and stop before the next one."""
        self.report(self.s.step(self._count(arg)))

    do_s = do_step
    do_next = do_step
    do_n = do_step

    def do_stepi(self, arg: str) -> None:
        """stepi [N]: run N sysclk cycles."""
        self.report(self.s.stepi(self._count(arg)))

    do_si = do_stepi

    def do_finish(self, arg: str) -> None:
        """finish: run until the current instruction completes (e.g. a field)."""
        self.report(self.s.finish())

    def do_until(self, arg: str) -> None:
        """until LOCATION: continue to a label, *address or line (temporary breakpoint)."""
        self.report(self.s.until(arg))

    do_advance = do_until

    # ---- breakpoints, watches, catches ------------------------------------------------
    def do_break(self, arg: str) -> None:
        """break LOCATION: stop before the instruction at a label, *address, or
        [file:]line issues."""
        bp = self.s.add_breakpoint(arg)
        ti = next(iter(bp.addrs))
        f, ln = self.s.line_of(bp.addr, ti)
        where = (" in " + ", ".join(self.s.names[i] for i in bp.addrs)
                 if len(self.s.cores) > 1 else "")
        self.out(f"Breakpoint {bp.id} at pc={bp.addr}{where}: file {f}, line {ln}.")

    do_b = do_break

    def do_tbreak(self, arg: str) -> None:
        """tbreak LOCATION: a breakpoint that deletes itself when hit."""
        bp = self.s.add_breakpoint(arg, temporary=True)
        self.out(f"Temporary breakpoint {bp.id} at pc={bp.addr}.")

    def do_delete(self, arg: str) -> None:
        """delete [N]: delete breakpoint/watchpoint/catchpoint N, or all."""
        self.s.delete(int(arg) if arg.strip() else None)

    do_d = do_delete

    def do_disable(self, arg: str) -> None:
        """disable N: disable breakpoint N."""
        self.s.enable(int(arg), False)

    def do_enable(self, arg: str) -> None:
        """enable N: enable breakpoint N."""
        self.s.enable(int(arg), True)

    def do_watch(self, arg: str) -> None:
        """watch EXPR: stop when a value changes (x, isr, flags.lost, pins.out.tx.level,
        fifo.rx.level, units.timing.phase, ...)."""
        w = self.s.add_watch(arg)
        self.out(f"Watchpoint {w.id}: {w.path}")

    def do_catch(self, arg: str) -> None:
        """catch event SOURCE[:KIND]: stop when a bench or engine event happens, e.g.
        `catch event partner:rx`, `catch event t0:push`, `catch event host:pop`."""
        parts = arg.split()
        if len(parts) != 2 or parts[0] != "event":
            raise DebugError("usage: catch event SOURCE[:KIND]")
        c = self.s.add_catch(parts[1])
        self.out(f"Catchpoint {c.id} (event {c.source}{':' + c.kind if c.kind else ''})")

    # ---- inspecting -------------------------------------------------------------------
    @staticmethod
    def _fmt_arg(arg: str) -> tuple[str, str]:
        if arg.startswith("/"):
            f, _, rest = arg[1:].partition(" ")
            return f, rest.strip()
        return "", arg.strip()

    def do_print(self, arg: str) -> None:
        """print[/x|/t|/d] EXPR: show a value (x, y, osr, isr, pc, flags, regs, fifo.tx,
        units.timing, pins, $cycle, $time)."""
        f, expr = self._fmt_arg(arg)
        self.out(f"$ = {fmt(self.s.eval(expr or 'regs'), f)}")

    do_p = do_print

    def do_display(self, arg: str) -> None:
        """display[/FMT] EXPR: print EXPR at every stop. `display` alone lists them."""
        f, expr = self._fmt_arg(arg)
        if not expr:
            for i, (e, ff) in self.displays.items():
                self.out(f"{i}: {e} = {fmt(self.s.eval(e), ff)}")
            return
        self.s.eval(expr)  # validate now
        i, self._next_display = self._next_display, self._next_display + 1
        self.displays[i] = (expr, f)
        self.out(f"{i}: {expr} = {fmt(self.s.eval(expr), f)}")

    def do_undisplay(self, arg: str) -> None:
        """undisplay [N]: stop displaying N, or all."""
        if arg.strip():
            self.displays.pop(int(arg), None)
        else:
            self.displays.clear()

    def do_core(self, arg: str) -> None:
        """core [NAME|N]: select the core that step, finish, print, info and x act
        on (as GDB's `thread`). Without an argument, show the selected one."""
        if arg.strip():
            self.s.select(arg.strip())
        t = self.s.core
        self.out(f"[Current core is {t.name} ({self.s.selected})]")
        self.show_where()

    do_thread = do_core

    def do_info(self, arg: str) -> None:
        """info cores|breakpoints|registers|flags|fifos|units [NAME]|pins|shared|
        events [N]|program|display|scenario|KEY. Any key of the core's state works
        (`info KEY` prints it), so a model's new state shows up here without changes."""
        what, _, rest = arg.strip().partition(" ")
        st = self.s.core_state()
        if what in ("cores", "threads"):
            for i, t in enumerate(self.s.cores):
                loc = self.s.where(i)
                mark = "*" if i == self.s.selected else " "
                state = f"  ({loc['stall']})" if loc["stall"] else ""
                self.out(f"{mark} {i}  {t.name:4s} pc={loc['pc']:<3d} {loc['instr']:28s} "
                         f"{loc['short']}{state}")
        elif what in ("b", "break", "breakpoints", "watchpoints"):
            if not (self.s.breakpoints or self.s.watches or self.s.catches):
                self.out("No breakpoints, watchpoints or catchpoints.")
            for b in self.s.breakpoints.values():
                ti = next(iter(b.addrs))
                f, ln = self.s.line_of(b.addr, ti)
                where = (" " + ",".join(self.s.names[i] for i in b.addrs)
                         if len(self.s.cores) > 1 else "")
                self.out(f"{b.id:<4d}{'tbreak' if b.temporary else 'break':8s}"
                         f"{'y' if b.enabled else 'n'}  pc={b.addr:<3d} {f}:{ln}{where}  "
                         f"hits={b.hits}")
            for w in self.s.watches.values():
                self.out(f"{w.id:<4d}watch   y  {w.path} = {fmt(w.last)}")
            for c in self.s.catches.values():
                self.out(f"{c.id:<4d}catch   y  event {c.source}{':' + c.kind if c.kind else ''}")
        elif what in ("r", "reg", "registers"):
            r = st["regs"]
            self.out(f"pc   {st['pc']:<12d} {st['label'] or ''}")
            for k in ("x", "y", "osr", "isr"):
                self.out(f"{k:4s} {hex(r[k]):12s} {r[k]}")
            self.out(f"osr_count {r['osr_count']}  isr_count {r['isr_count']}  "
                     f"irq {hex(st['irq'])}  stall '{st['stall']}'  delay {st['delay_left']}")
        elif what == "flags":
            self.out("  ".join(f"{k}={int(v)}" for k, v in st["flags"].items()))
        elif what in ("fifo", "fifos"):
            for k, f in st["fifo"].items():
                self.out(f"{k}: {f['level']}/{f['depth']}  {[hex(w) for w in f['words']]}")
        elif what in ("units", "shared"):
            units = st["units"] if what == "units" else self.s.state()["shared"]
            if not units:
                self.out(f"(no {'shared units' if what == 'shared' else 'units'})")
            for name, u in units.items():
                if rest and name != rest:
                    continue
                self.out(f"{name}: " + ", ".join(f"{k}={fmt(v, 'd')}" if not isinstance(
                    v, (dict, list)) else f"{k}={v}" for k, v in u.items()))
        elif what == "pins":
            ins = ", ".join(f"{k}={v}" for k, v in st["pins"]["in"].items()) or "(none)"
            outs = ", ".join(f"{k}={v['level']}{'' if v['oe'] else ' (released)'}"
                             for k, v in st["pins"]["out"].items()) or "(none)"
            self.out(f"in (synchronised): {ins}")
            self.out(f"out: {outs}")
        elif what == "events":
            n = int(rest) if rest else 20
            for src, t in list(self.s.history)[-n:]:
                self.out(format_event(src, t))
        elif what == "program":
            self._disassemble(0, len(self.s.program))
        elif what == "display":
            self.do_display("")
        elif what == "scenario":
            sc = self.s.scenario
            self.out(f"{sc.name}: devices {[a.device.name for a in sc.devices]}, "
                     f"max_cycles {sc.max_cycles}, cycle {self.s.cycle}")
        elif what in st:
            v = st[what]
            if isinstance(v, dict):
                for k, x in v.items():
                    self.out(f"{k}: {fmt(x)}")
            else:
                self.out(fmt(v))
        else:
            raise DebugError("info cores|breakpoints|registers|flags|fifos|units [NAME]|"
                             "pins|shared|events [N]|program|display|scenario|<state key: "
                             f"{', '.join(k for k in st if isinstance(st[k], dict))}>")

    def _disassemble(self, start: int, n: int) -> None:
        pc = self.s.core.pc
        ti = self.s.selected
        bps = {b.addrs[ti] for b in self.s.breakpoints.values() if b.enabled and ti in b.addrs}
        for row in self.s.disassemble(start, n):
            if row is None:
                continue
            a = row["addr"]
            if row["label"]:
                self.out(f"   {row['label']}:")
            mark = ("=>" if a == pc else "  ") + ("B" if a in bps else " ")
            self.out(f"{mark} {a:3d}  {row['text']:32s} ; {os.path.basename(row['file'])}"
                     f":{row['line']}")

    def do_x(self, arg: str) -> None:
        """x/Ni [ADDR]: disassemble N instructions from ADDR (default: the PC)."""
        f, rest = self._fmt_arg(arg)
        n = int(f.rstrip("i") or 4)
        start = self.s.resolve(rest) if rest else self.s.core.pc
        self._disassemble(start, n)

    def do_list(self, arg: str) -> None:
        """list [LOCATION]: source around the PC (or a label / line)."""
        addr = self.s.resolve(arg) if arg.strip() else self.s.core.pc
        f, ln = self.s.line_of(addr)
        _, lines = self.s.source_lines(f)
        pc_line = self.s.line_of(self.s.core.pc)[1]
        ti = self.s.selected
        bp_lines = {self.s.line_of(b.addrs[ti])[1] for b in self.s.breakpoints.values()
                    if ti in b.addrs}
        for i in range(max(1, ln - 5), min(len(lines), ln + 5) + 1):
            mark = ("=>" if i == pc_line else "  ") + ("B" if i in bp_lines else " ")
            self.out(f"{mark} {i:<5d} {lines[i - 1]}")

    do_l = do_list

    def do_backtrace(self, arg: str) -> None:
        """backtrace: the single frame (the engine has no call stack)."""
        w = self.s.where()
        self.out(f"#0  {self._who()}pc={w['pc']} {w['label'] or ''} {w['instr']}  at "
                 f"{w['short']}  cycle {w['cycle']}")

    do_bt = do_backtrace
    do_where = do_backtrace

    def do_set(self, arg: str) -> None:
        """set var REG = VALUE (x, y, osr, isr, pc)  |  set trace on|off (print every
        event while running)."""
        parts = arg.split(None, 1)
        if parts and parts[0] == "trace":
            self.trace = len(parts) > 1 and parts[1].strip() in ("on", "1", "yes")
            self.out(f"trace {'on' if self.trace else 'off'}")
            return
        if parts and parts[0] == "var" and len(parts) > 1 and "=" in parts[1]:
            name, _, value = parts[1].partition("=")
            self.s.set_var(name.strip(), int(value.strip(), 0))
            return
        raise DebugError("set var REG = VALUE | set trace on|off")

    def do_vcd(self, arg: str) -> None:
        """vcd FILE: write the waveform so far to FILE and rewrite it at every stop (open it
        in GTKWave or Surfer and reload to follow along)  |  vcd off  |  vcd: show."""
        a = arg.strip()
        if a == "off":
            self.s.set_vcd(None)
            self.out("vcd off")
        elif a:
            self.s.set_vcd(a)
            self.out(f"vcd: {a} (cycle {self.s.cycle}; rewritten at every stop)")
        else:
            self.out(f"vcd: {self.s.vcd_path or 'off'}")

    def do_quit(self, arg: str) -> bool:
        """quit: leave the debugger."""
        self.quit = True
        return True

    do_q = do_quit
    do_EOF = do_quit


def debug_main(argv: Sequence[str] | None) -> int:
    import argparse
    import sys

    from piolab.asm import AsmErrors
    from piolab.devices.spec import DeviceSpecError
    from piolab.packs import UnknownModel
    from piolab.run import RunError, RunOptions, add_run_args

    ap = argparse.ArgumentParser(
        prog="piolab debug",
        description="Step through a program on its ISA pack's engine model, GDB style.")
    add_run_args(ap, "program: source or image JSON")
    ap.add_argument("-ex", dest="ex", action="append", default=[], metavar="CMD",
                    help="run a debugger command at start (repeatable)")
    ap.add_argument("--commands", "-x", type=argparse.FileType("r"), metavar="FILE",
                    help="run the debugger commands in FILE at start")
    ap.add_argument("--batch", action="store_true", help="exit after the start-up commands")
    a = ap.parse_args(argv)
    opts = RunOptions.from_args(a)
    try:
        factory = opts.factory()
        session = DebugSession(factory, dut=opts.dut, model=opts.model)
    except AsmErrors as e:
        print(e.render(), file=sys.stderr)
        return 1
    except (RunError, DeviceSpecError, UnknownModel, ValueError, OSError) as e:
        print(f"piolab debug: {e}", file=sys.stderr)
        return 2
    vcd = opts.vcd_path(session.scenario.name)
    if vcd:
        session.set_vcd(vcd)
    repl = Repl(session)
    cmds = [ln.strip() for ln in (a.commands.read().splitlines() if a.commands else [])
            if ln.strip() and not ln.lstrip().startswith("#")] + list(a.ex)
    if a.batch or cmds:
        for c in cmds:
            repl.out(f"{repl.prompt}{c}")
            if repl.onecmd(c):
                return 0
        if a.batch:
            return 0
    try:
        repl.cmdloop(None if cmds else repl.intro)
    except KeyboardInterrupt:
        repl.out("")
    return 0

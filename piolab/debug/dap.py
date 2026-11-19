"""`piolab dap`: a Debug Adapter Protocol server (stdio) for VS Code, over DebugSession.

No dependencies: DAP messages are JSON with a `Content-Length` header. The VS Code
side is the small extension in piolab/vscode/, which starts `uv run piolab dap`.

Mapping to the debugger:
  Continue -> cont      Step Over (next) -> step (one instruction)
  Step Into -> stepi (one sysclk cycle)  Step Out -> finish
Breakpoints are set on lines of program source files. Data breakpoints (right-click a variable,
"Break on Value Change") are watches. The Debug Console accepts debugger commands
(`info units`, `p/x isr`, `x/8i`, `catch event partner:rx`, ...) or a state path.

Everything shown is derived from the model (ADR-0010): one thread per core, one
scope per dict-valued key of that core's state, a scope for its scalars, and one
for the shared units when there are any. Nothing here names a register or a unit.

Instruction addresses (disassembly, instruction breakpoints) carry the core in
the bits above the word address: 0xTAAA = core T, address AAA.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import threading
import traceback
from collections.abc import Callable
from typing import IO, Any

from piolab.asm import AsmErrors
from piolab.bench.report import format_event
from piolab.devices.base import Txn

from .repl import Repl
from .session import DebugError, DebugSession, Stop

#: Display titles for well-known state keys; any other key is shown title-cased.
TITLES = {"regs": "Registers", "fifo": "FIFOs", "pins": "Pins", "units": "Units",
          "flags": "Flags"}
ADDR_BITS = 12  # instruction address bits below the core index in an address


def addr_ref(ti: int, addr: int) -> str:
    return f"0x{(ti << ADDR_BITS) | addr:04x}" if addr >= 0 else str(addr)


def parse_addr_ref(ref: str) -> tuple[int, int]:
    v = int(ref, 0)
    if v < 0:
        return 0, v
    return v >> ADDR_BITS, v & ((1 << ADDR_BITS) - 1)


_STOP_REASON = {"breakpoint": "breakpoint", "watch": "data breakpoint", "catch": "breakpoint",
                "step": "step", "pause": "pause", "entry": "entry", "limit": "step",
                "error": "exception"}


def factory_from_launch(args: dict[str, Any]) -> Callable[[], Any]:
    """The scenario of a launch configuration: the same run description as `piolab sim`
    (`program` or `scenario`, `devices` specs, `hostTx`, `cycles`)."""
    from piolab.run import RunOptions

    return RunOptions.from_launch(args).factory()


def dut_from_launch(args: dict[str, Any]) -> str:
    """The DUT: launch attribute "dut" (engine: the program's ISA pack's model, a model
    name, or package.module:callable)."""
    return str(args.get("dut") or "engine")


class DapServer:
    def __init__(self, rfile: IO[bytes], wfile: IO[bytes]) -> None:
        self.rfile, self.wfile = rfile, wfile
        self.seq = 1
        self.wlock = threading.Lock()
        self.slock = threading.Lock()
        self.s: DebugSession | None = None
        self.stop_on_entry = True
        self.trace = False
        self.refs: dict[int, Any] = {}
        self.worker: threading.Thread | None = None
        self.done = False

    # ---- framing ----------------------------------------------------------------------
    def send(self, msg: dict[str, Any]) -> None:
        with self.wlock:
            msg["seq"] = self.seq
            self.seq += 1
            body = json.dumps(msg).encode()
            self.wfile.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            self.wfile.flush()

    def read(self) -> dict[str, Any] | None:
        length = None
        while True:
            line = self.rfile.readline()
            if not line:
                return None
            line = line.strip()
            if not line:
                break
            k, _, v = line.decode().partition(":")
            if k.lower() == "content-length":
                length = int(v.strip())
        if length is None:
            return None
        msg: dict[str, Any] = json.loads(self.rfile.read(length))
        return msg

    def respond(self, req: dict[str, Any], body: dict[str, Any] | None = None,
                ok: bool = True, message: str = "") -> None:
        r: dict[str, Any] = {"type": "response", "request_seq": req["seq"], "success": ok,
                             "command": req["command"]}
        if body is not None:
            r["body"] = body
        if message:
            r["message"] = message
        self.send(r)

    def event(self, name: str, body: dict[str, Any] | None = None) -> None:
        e: dict[str, Any] = {"type": "event", "event": name}
        if body is not None:
            e["body"] = body
        self.send(e)

    def output(self, text: str, category: str = "console") -> None:
        self.event("output", {"category": category, "output": text if text.endswith("\n")
                              else text + "\n"})

    # ---- main loop --------------------------------------------------------------------
    def serve(self) -> None:
        while not self.done:
            msg = self.read()
            if msg is None:
                break
            if msg.get("type") != "request":
                continue
            cmd = msg["command"]
            fn = getattr(self, "req_" + cmd, None)
            try:
                if fn is None:
                    self.respond(msg, ok=False, message=f"unsupported request '{cmd}'")
                else:
                    fn(msg, msg.get("arguments") or {})
            except (DebugError, AsmErrors, SystemExit, ValueError, KeyError) as e:
                self.respond(msg, ok=False, message=str(e))
            except Exception as e:  # report, keep serving
                self.output(traceback.format_exc(), "stderr")
                self.respond(msg, ok=False, message=f"{type(e).__name__}: {e}")

    @property
    def sess(self) -> DebugSession:
        if self.s is None:
            raise DebugError("no program launched")
        return self.s

    # ---- running (in a worker thread, so `pause` is heard) ---------------------------
    def _run(self, fn: Callable[[], Stop]) -> None:
        if self.worker is not None and self.worker.is_alive():
            raise DebugError("already running")

        def work() -> None:
            with self.slock:
                st = fn()
            self._stopped(st)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _stopped(self, st: Stop) -> None:
        self.refs.clear()
        s = self.sess
        if st.reason == "finished":
            r = s.result()
            self.output(f"[scenario '{r.scenario}' finished after {r.cycles} cycles]")
            for tc, words in r.host_rx.items():
                if words:
                    self.output(f"  host rx t{tc}: {[hex(w) for w in words]}")
            self.event("terminated")
            return
        text = ""
        d = st.detail
        if st.reason == "watch":
            text = f"{d['path']}: {d['old']} -> {d['new']}"
        elif st.reason == "catch":
            text = f"event {d['source']}:{d['event'].kind}"
            self.output(format_event(d["source"], d["event"]).strip())
        elif st.reason == "error":
            text = d["error"]
            self.output(f"engine error: {text}", "stderr")
        elif st.reason == "limit":
            text = f"stopped after {d['cycles']} cycles"
        body: dict[str, Any] = {"reason": _STOP_REASON.get(st.reason, "step"),
                                "threadId": int(d.get("core", s.selected)) + 1,
                                "allThreadsStopped": True,
                                "description": text or st.reason}
        if text:
            body["text"] = text
        if st.reason == "breakpoint":
            body["hitBreakpointIds"] = [d["id"]]
        self.event("stopped", body)

    def _on_event(self, src: str, t: Txn) -> None:
        if self.trace:
            self.output(format_event(src, t))

    # ---- requests ---------------------------------------------------------------------
    def req_initialize(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self.respond(req, {
            "supportsConfigurationDoneRequest": True, "supportsEvaluateForHovers": True,
            "supportsSetVariable": True, "supportsDataBreakpoints": True,
            "supportsRestartRequest": True, "supportsTerminateRequest": True,
            "supportsSteppingGranularity": False, "supportsDisassembleRequest": True,
            "supportsInstructionBreakpoints": True})

    def req_launch(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self.stop_on_entry = bool(a.get("stopOnEntry", True))
        self.trace = bool(a.get("trace", False))
        self.s = DebugSession(factory_from_launch(a), dut=dut_from_launch(a),
                              model=a.get("model") or None)
        self.s.on_event = self._on_event
        if a.get("vcd"):
            self.s.set_vcd(os.path.abspath(a["vcd"]))
        self.respond(req)
        self.event("initialized")
        sc = self.s.scenario
        if self.s.vcd_path is not None:
            self.output(f"piolab: waveform rewritten at every stop: {self.s.vcd_path}")
        self.output(f"piolab: {sc.name} with devices {[x.device.name for x in sc.devices]}, "
                    f"cores {self.s.names}; "
                    "Step Over = one instruction, Step Into = one sysclk cycle, "
                    "Step Out = finish the current instruction. The Debug Console takes "
                    "debugger commands (help).")

    def req_setBreakpoints(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        path = a.get("source", {}).get("path", "")
        lines = [b["line"] for b in a.get("breakpoints", [])]
        out = []
        for ln, bp in zip(lines, self.sess.set_line_breakpoints(path, lines), strict=True):
            if bp is None:
                out.append({"verified": False, "line": ln, "message": "no code at or after "
                            "this line"})
            else:
                f, actual = self.sess.line_of(bp.addr, next(iter(bp.addrs)))
                out.append({"verified": True, "id": bp.id, "line": actual,
                            "source": {"path": os.path.abspath(f)}})
        self.respond(req, {"breakpoints": out})

    def req_setInstructionBreakpoints(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        refs = []
        for b in a.get("breakpoints", []):
            ti, addr = parse_addr_ref(b["instructionReference"])
            refs.append((ti, addr + int(b.get("offset", 0))))
        out = []
        for (ti, _), bp in zip(refs, self.sess.set_instruction_breakpoints(refs), strict=True):
            out.append({"verified": False, "message": "no instruction at this address"}
                       if bp is None else {"verified": True, "id": bp.id,
                                           "instructionReference": addr_ref(ti, bp.addr)})
        self.respond(req, {"breakpoints": out})

    def req_disassemble(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        """A core's instruction memory: one row per slot (word addresses)."""
        ti, base = parse_addr_ref(a["memoryReference"])
        start = base + int(a.get("offset", 0)) + int(a.get("instructionOffset", 0))
        out = []
        rows = self.sess.disassemble(start, int(a["instructionCount"]), ti)
        for i, r in enumerate(rows):
            if r is None:
                out.append({"address": addr_ref(ti, start + i), "instruction": "",
                            "presentationHint": "invalid"})
                continue
            row: dict[str, Any] = {
                "address": addr_ref(ti, r["addr"]), "instruction": r["text"],
                "line": r["line"],
                "location": {"path": os.path.abspath(r["file"]),
                             "name": os.path.basename(r["file"])}}
            if r["word"] is not None:
                row["instructionBytes"] = f"{r['word']:0{(r['word_bits'] + 3) // 4}x}"
            if r["label"]:
                row["symbol"] = r["label"]
            out.append(row)
        self.respond(req, {"instructions": out})

    def req_setExceptionBreakpoints(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self.respond(req, {"breakpoints": []})

    def req_configurationDone(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self.respond(req)
        if self.stop_on_entry:
            self._stopped(Stop("entry", 0))
        else:
            self._run(self.sess.cont)

    def req_threads(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self.respond(req, {"threads": [{"id": i + 1, "name": n}
                                       for i, n in enumerate(self.sess.names)]})

    @staticmethod
    def _ti(a: dict[str, Any], key: str = "threadId") -> int | None:
        """Thread and frame ids are both core index + 1 (one frame per core)."""
        v = a.get(key)
        return int(v) - 1 if v else None

    def req_stackTrace(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        ti = self._ti(a) or 0
        w = self.sess.where(ti)
        name = f"{w['label'] + ': ' if w['label'] else ''}{w['instr']}  (cycle {w['cycle']})"
        frame = {"id": ti + 1, "name": name, "line": w["line"], "column": 1,
                 "source": {"path": os.path.abspath(w["file"]),
                            "name": os.path.basename(w["file"])},
                 "instructionPointerReference": addr_ref(ti, w["pc"])}
        self.respond(req, {"stackFrames": [frame], "totalFrames": 1})

    def _ref(self, node: Any, path: str) -> int:
        i = len(self.refs) + 100
        self.refs[i] = (node, path)
        return i

    def req_scopes(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        """Scopes are read off the model's state: the core's scalars, then one per
        dict-valued key (Registers, Flags, FIFOs, Units, Pins, and anything a model adds),
        the shared units if any, and the time."""
        ti = self._ti(a, "frameId") or 0
        name = self.sess.names[ti]
        ts = self.sess.core_state(ti)
        full = self.sess.state()
        scopes = [(f"Core {name}",
                   {k: v for k, v in ts.items() if not isinstance(v, (dict, list))}, name)]
        scopes += [(TITLES.get(k, k.replace("_", " ").title()), v, f"{name}.{k}")
                   for k, v in ts.items() if isinstance(v, dict)]
        if full.get("shared"):
            scopes.append(("Shared", full["shared"], "shared"))
        scopes.append(("Timing", {"cycle": full["cycle"],
                                  "time_us": round(full["time_s"] * 1e6, 4)}, ""))
        self.respond(req, {"scopes": [
            {"name": title, "variablesReference": self._ref(node, path), "expensive": False}
            for title, node, path in scopes]})

    def req_variables(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        node, path = self.refs.get(a["variablesReference"], ({}, ""))
        items = node.items() if isinstance(node, dict) else enumerate(node)
        out = []
        for k, v in items:
            p = f"{path}.{k}" if path else str(k)
            if isinstance(v, (dict, list)) and v:
                summary = "{…}" if isinstance(v, dict) else f"[{len(v)}]"
                out.append({"name": str(k), "value": summary,
                            "variablesReference": self._ref(v, p)})
            else:
                big_int = isinstance(v, int) and not isinstance(v, bool) and v > 9
                shown = hex(v) if big_int else str(v)
                out.append({"name": str(k), "value": shown, "variablesReference": 0,
                            "evaluateName": p})
        self.respond(req, {"variables": out})

    def req_setVariable(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        value = int(str(a["value"]), 0)
        _, path = self.refs.get(a.get("variablesReference", 0), ({}, ""))
        self.sess.set_var(f"{path}.{a['name']}" if path else a["name"], value)
        self.respond(req, {"value": hex(value)})

    def req_dataBreakpointInfo(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        ref = a.get("variablesReference")
        _, path = self.refs.get(ref, ({}, "")) if ref else ({}, "")
        name = a["name"]
        p = f"{path}.{name}" if path else name
        try:
            self.sess.eval(p)
        except DebugError:
            self.respond(req, {"dataId": None, "description": f"cannot watch {name}"})
            return
        self.respond(req, {"dataId": p, "description": p, "accessTypes": ["write"]})

    def req_setDataBreakpoints(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        s = self.sess
        for i in list(s.watches):
            s.delete(i)
        out = []
        for b in a.get("breakpoints", []):
            w = s.add_watch(b["dataId"])
            out.append({"verified": True, "id": w.id})
        self.respond(req, {"breakpoints": out})

    def req_continue(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self.respond(req, {"allThreadsContinued": True})
        self._run(self.sess.cont)

    def _select(self, a: dict[str, Any]) -> None:
        ti = self._ti(a)
        if ti is not None:
            self.sess.select(ti)

    def req_next(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self._select(a)
        self.respond(req)
        self._run(self.sess.step)

    def req_stepIn(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self._select(a)
        self.respond(req)
        self._run(self.sess.stepi)

    def req_stepOut(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self._select(a)
        self.respond(req)
        self._run(self.sess.finish)

    def req_pause(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        self.sess.pause_requested = True
        self.respond(req)

    def req_restart(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        with self.slock:
            self.sess.restart()
        self.respond(req)
        self._stopped(Stop("entry", 0))

    def req_evaluate(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        expr = a.get("expression", "").strip()
        ti = self._ti(a, "frameId")
        if a.get("context") == "repl" and expr:
            if ti is not None:
                self.sess.select(ti)
            word = expr.split()[0].split("/")[0]
            if hasattr(Repl, "do_" + word):
                if word in ("continue", "c", "step", "s", "next", "n", "stepi", "si", "finish",
                            "until", "advance", "run", "quit", "q"):
                    raise DebugError("use the debug toolbar to run or step")
                buf = io.StringIO()
                with self.slock:
                    Repl(self.sess, stdout=buf).onecmd(expr)
                self.sess.on_event = self._on_event  # Repl() took it over
                self.respond(req, {"result": buf.getvalue().rstrip() or "(ok)",
                                   "variablesReference": 0})
                return
        v = self.sess.eval(expr, ti)
        if isinstance(v, (dict, list)):
            self.respond(req, {"result": json.dumps(v, default=str)[:200],
                               "variablesReference": self._ref(v, expr)})
        else:
            shown = hex(v) if isinstance(v, int) and not isinstance(v, bool) else str(v)
            self.respond(req, {"result": shown, "variablesReference": 0})

    def req_disconnect(self, req: dict[str, Any], a: dict[str, Any]) -> None:
        if self.s is not None:
            self.s.pause_requested = True
        self.respond(req)
        self.done = True

    req_terminate = req_disconnect


def dap_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="piolab dap",
                                 description="Debug Adapter Protocol server on stdio (VS Code).")
    ap.parse_args(argv)
    DapServer(sys.stdin.buffer, sys.stdout.buffer).serve()
    return 0

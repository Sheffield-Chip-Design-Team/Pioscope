// PIOLab assembly: registers the `piolab` debug type, whose adapter is `uv run piolab dap`
// (piolab/debug/dap.py) run in the PIOLab project directory. Plain JS, no dependencies.
"use strict";
const vscode = require("vscode");
const fs = require("fs");
const path = require("path");

/** The nearest directory at or above `start` that holds pyproject.toml. */
function projectDir(start) {
  let dir = start;
  for (let i = 0; i < 20 && dir; i++) {
    if (fs.existsSync(path.join(dir, "pyproject.toml"))) return dir;
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return undefined;
}

function activate(context) {
  context.subscriptions.push(
    vscode.debug.registerDebugConfigurationProvider("piolab", {
      resolveDebugConfiguration(folder, config) {
        // F5 with no launch.json: debug the active program
        if (!config.type && !config.request && !config.name) {
          const ed = vscode.window.activeTextEditor;
          if (ed && ed.document.languageId === "piolab-asm") {
            Object.assign(config, {
              type: "piolab", request: "launch", name: "Debug current program",
              program: ed.document.uri.fsPath, devices: ["uart:send=0x55,0xA3"],
              hostTx: ["0x55", "0xA3"], stopOnEntry: true,
            });
          }
        }
        if (!config.program && !config.scenario) {
          return vscode.window
            .showInformationMessage('PIOLab: set "program" (a program source file) or "scenario" in launch.json')
            .then(() => undefined);
        }
        if (!config.cwd) {
          const from = config.program ? path.dirname(config.program)
            : folder ? folder.uri.fsPath : undefined;
          config.cwd = (from && projectDir(from)) || (folder && projectDir(folder.uri.fsPath))
            || (folder && folder.uri.fsPath);
        }
        return config;
      },
    })
  );
  context.subscriptions.push(
    vscode.debug.registerDebugAdapterDescriptorFactory("piolab", {
      createDebugAdapterDescriptor(session) {
        const cfg = vscode.workspace.getConfiguration("piolab");
        let cmd = cfg.get("adapterCommand") || "uv";
        if (cmd.startsWith("~/")) cmd = path.join(require("os").homedir(), cmd.slice(2));
        const args = cfg.get("adapterArgs") || ["run", "piolab", "dap"];
        return new vscode.DebugAdapterExecutable(cmd, args, { cwd: session.configuration.cwd });
      },
    })
  );
}

function deactivate() {}

module.exports = { activate, deactivate };

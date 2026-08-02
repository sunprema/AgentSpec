/* AgentSpec VS Code shim: launch `aspec lsp` over stdio for .aspec.py files.
   The server does the work; this extension only wires the transport. */
const { workspace } = require("vscode");
const { LanguageClient } = require("vscode-languageclient/node");

let client;

function activate() {
  const command = workspace.getConfiguration("aspec").get("serverCommand");
  client = new LanguageClient(
    "aspec",
    "AgentSpec",
    { command: command[0], args: command.slice(1), options: { cwd: workspace.rootPath } },
    { documentSelector: [{ scheme: "file", pattern: "**/*.aspec.py" }] }
  );
  client.start();
}

function deactivate() {
  return client ? client.stop() : undefined;
}

module.exports = { activate, deactivate };

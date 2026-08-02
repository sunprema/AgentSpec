# AgentSpec for VS Code

A thin shim that launches `aspec lsp` (the AgentSpec language server) for
`.aspec.py` files. All features live in the server: diagnostics (parser
P0xx + lint AS0xx), hover, go-to-definition, document outline, completions
(`var.` result fields, `env.`, enum values), and the "add a why" quickfix.

## Build and install

```sh
cd editors/vscode
npm install
npx @vscode/vsce package        # produces aspec-lsp-0.1.0.vsix
code --install-extension aspec-lsp-0.1.0.vsix
```

The server command defaults to `uv run aspec lsp` and is configurable via
the `aspec.serverCommand` setting — point it at an absolute `aspec` binary
when the workspace is not a uv project.

## Other editors

No extension needed — any LSP client works. Neovim:

```lua
vim.api.nvim_create_autocmd({ "BufReadPost", "BufNewFile" }, {
  pattern = "*.aspec.py",
  callback = function()
    vim.lsp.start({ name = "aspec", cmd = { "uv", "run", "aspec", "lsp" } })
  end,
})
```

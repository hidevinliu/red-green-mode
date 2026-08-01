# MCP build-vs-buy: your red-green setup should ship its own first-party thin server

## Verdict

The **core MCP server of a red-green architecture has to be first-party**: `red-green-mode` maintains its own thin MCP server that exposes your CodeMap, Context Pack, read-only explorer, verifier, and gate boundaries directly.

You can use other authors' MCP servers, but only as **adapters / context providers**. They never get to judge red-green completion.

## Why not just call RepoPrompt CE's MCP server

RepoPrompt CE's MCP server is strong, and it fits its own app / workspace / context-builder model. But your red-green architecture has its own completion semantics:

```text
Run Ledger + external verifier + rgm_gate.py RGM_GATE=PASS
```

Making an external MCP server a core dependency creates three problems:

1. **You don't own completion semantics**: an external server's summary or context builder cannot replace your gate.
2. **You don't own the release cadence**: it upgrades, changes its schema, or renames tools, and your red-green loop gets dragged along.
3. **You don't own the security boundary**: write tools, worktrees, file read scope, and secret/PII protection all have to follow your rules.

So the correct structure is:

```text
RGM first-party MCP server      ← core capability, maintained by you
  ├─ repo.codemap.build
  ├─ repo.context.select
  ├─ repo.read.slice
  ├─ repo.diff.summary
  ├─ repo.verify.run
  ├─ repo.patch.apply          ← hidden by default; even under RGM_MCP_ENABLE_WRITE=1 it stays behind ledger/context/hypothesis guards
  └─ agent.explore.read_only

External MCP servers            ← optional adapters
  ├─ RepoPrompt CE MCP
  ├─ GitHub MCP
  ├─ Filesystem MCP
  └─ Browser/Search MCP
```

## Referencing the official MCP protocol / SDK

Key points from the official MCP Tools spec:

- A server advertises tools through `tools/list`.
- A client invokes them through `tools/call`.
- A tool result may return `content` and `structuredContent` together.
- A server must declare the tools capability.
- A server should validate input, enforce permissions, restrict sensitive operations, and give users a chance to confirm sensitive calls.

Where the official Python SDK fits: it can build MCP servers and clients over stdio, Streamable HTTP, and SSE. Current policy:

- Keep the core on a stdlib JSON-RPC stdio server to minimize dependencies and installation failure modes.
- Later, add `rgm_mcp_sdk_server.py` as an SDK adapter — but pin a stable version.
- Neither the SDK nor any third-party server changes who judges red-green completion.

## What ships today

| Tool | Purpose |
|---|---|
| `tools/rgm_mcp_server.py` | First-party MCP stdio server: read-only by default, write tools explicitly env-gated |
| `tools/rgm_mcp_profile.py config` | Generates the `mcpServers` config snippet |
| `tools/rgm_mcp_profile.py strategy` | Prints the build-vs-buy policy |
| `tools/rgm_mcp_smoke.py --context-call` | Runs the protocol smoke: initialize / notifications/initialized / ping / tools/list / tools/call |
| `tools/rgm_mcp_smoke.py --write-call` | Additionally verifies that `repo.patch.apply` appears only under `RGM_MCP_ENABLE_WRITE=1` and that the patch returns `not_completion_proof=true` |
| `tools/rgm_mcp_adapters.py registry` | Lists external MCP adapter profiles and their forbidden boundaries |
| `tools/rgm_mcp_adapters.py plan` | Generates an adapter-only usage plan for a given external MCP |
| `tools/rgm_mcp_adapters.py host-check` | Checks whether a real MCP host config contains the first-party core, and demotes external servers to adapter_only |

## Installation

```bash
python3 tools/rgm_mcp_profile.py config --root . --name rgm-readonly-repo
```

The output drops into any MCP-capable host config:

```json
{
  "mcpServers": {
    "rgm-readonly-repo": {
      "command": "python3",
      "args": ["/abs/path/to/tools/rgm_mcp_server.py"],
      "env": {"RGM_MCP_ROOT": "/abs/path/to/red-green-mode"}
    }
  }
}
```

Leave `--allow-verify` off by default. Add it only when you deliberately want the host to run the verifier through MCP:

```bash
python3 tools/rgm_mcp_profile.py config --allow-verify
```

Even then, `repo.verify.run` is not proof of completion; you still need `rgm_gate.py RGM_GATE=PASS`.

Likewise, leave write tools off in the host config by default. Generate a config carrying the write-tool env only when you explicitly want the MCP host to call the write-gated patch tool:

```bash
python3 tools/rgm_mcp_profile.py config --enable-write
```

That is equivalent to adding this to the server's env:

```json
{"RGM_MCP_ENABLE_WRITE": "1"}
```

All it does is put `repo.patch.apply` into `tools/list`. Calls still have to supply `ledger`, a non-empty `hypothesis`, and `context_pack`, and the patch targets still have to sit inside the selected/risk/dependency boundary. The write tool returns only a diff summary plus `not_completion_proof=true`; it replaces neither the verifier nor the gate.

## Smoke

```bash
python3 tools/rgm_mcp_smoke.py --root . --task "enter red-green mode and get pytest to all green" --context-call
python3 tools/rgm_mcp_smoke.py --root . --task "write-gated MCP" --context-call --write-call
```

Expected:

```text
MCP_SMOKE=PASS
PROTOCOL_VERSION=2025-11-25
WRITE_TOOLS_EXPOSED_BY_DEFAULT=false
OWNERSHIP_STRATEGY=first_party_thin_server
MCP_WRITE_SMOKE=PASS              # only with --write-call
PATCH_NOT_COMPLETION_PROOF=true   # only with --write-call
```



## External MCP adapter registry

```bash
python3 tools/rgm_mcp_adapters.py registry
python3 tools/rgm_mcp_adapters.py plan --adapter repoprompt-ce --task "fix auth token refresh" --root .
python3 tools/rgm_mcp_adapters.py host-check --config /path/to/mcp-config.json --root .
```

`host-check` doesn't pass because "all the external MCP servers are reachable." It passes when:

1. The config contains the RGM first-party core server (default name `rgm-readonly-repo`).
2. That core points at `rgm_mcp_server.py` and sets `RGM_MCP_ROOT`.
3. Every other MCP server is classified as `adapter_only`, and its output cannot become a completion proof.

With the core missing, it prints:

```text
MCP_HOST_CHECK=FAIL
```

At that point, do not treat RepoPrompt CE, GitHub, Browser, or any other external MCP as the red-green core.

## Roadmap

1. Real MCP host integration testing: Claude Desktop, Cursor, Codex MCP hosts, and others.
2. SDK adapter: pin a stable version of the official `mcp` Python SDK and build an optional `rgm_mcp_sdk_server.py`.
3. End-to-end replay against a real host: once host-check passes, call `mcp.host_status` / `tools/list` / the read-only explorer inside an actual MCP host.
4. Write-tool expansion: `repo.patch.apply` is already wired behind env + ledger + context boundaries; any future write tool must be equally disabled-by-default, write-gated, and not-completion-proof.

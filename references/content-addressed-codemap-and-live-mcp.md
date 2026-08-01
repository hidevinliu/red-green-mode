# What v0.15–v0.18 absorbed: content-addressed CodeMap cache + first-party live MCP

## Why absorb it

RepoPrompt CE's mainline has already pushed the CodeMap toward content-addressed artifacts. For a red-green architecture, the value of that direction isn't that it's cooler — it's that:

1. **Context becomes reusable**: unchanged files don't get reparsed.
2. **Invalidation becomes explainable**: rebuilds happen only when the content hash or the pipeline hash changes.
3. **Handoffs become auditable**: a context pack can point at a stable artifact key.
4. **Long runs get steadier**: after a cold start you can restore the repo skeleton from disk instead of chat history.

## Already shipped

| Capability | Tool | Result |
|---|---|---|
| content-addressed artifact cache | `tools/rgm_codemap.py build` | `.rgm/codemap-artifacts/<sha>/<key>.json` |
| cache hit/miss/write stats | `codemap.cache` | Flows into `.rgm/codemap.json/md` and the context pack |
| cache invalidation | content sha + pipeline sha | Any change in content or parser identity is a miss |
| first-party live MCP server | `tools/rgm_mcp_server.py` | MCP `2025-11-25` stdio JSON-RPC; read-only by default, with `repo.patch.apply` exposed only under `RGM_MCP_ENABLE_WRITE=1` |
| MCP install profile | `tools/rgm_mcp_profile.py` | Generates the `mcpServers` config and the build-vs-buy policy; `--enable-write` explicitly adds the write-tool env gate |
| MCP protocol smoke | `tools/rgm_mcp_smoke.py` | Checks initialize / tools/list / tools/call / structuredContent / the read-only boundary; `--write-call` checks the write-gated patch |
| read-only explorer lane | `agent.explore.read_only` | Produces a CodeMap / context pack / risk / test / unknowns summary |

## Boundaries

- The current cache artifacts still come from a regex/heuristic parser, not a Tree-sitter/AST-level one.
- The live MCP server is a first-party thin server and exposes no write tools by default. `repo.patch.apply` is wired up but requires `RGM_MCP_ENABLE_WRITE=1` plus a ledger `PLAN_ONE_FIX`/hypothesis plus the `context_pack` selected/risk/dependency boundary.
- `repo.patch.apply` accepts unified diffs only and rejects path escapes, deletes, renames, chmods, and obvious test skip/xfail anti-cheat patterns; even on success it returns nothing but a diff summary plus `not_completion_proof=true`.
- `repo.verify.run` is disabled by default so nobody mistakes the live server for proof of completion; even under `RGM_MCP_ALLOW_VERIFY=1` you still have to run `rgm_gate.py`.
- External MCP servers such as RepoPrompt CE, GitHub, and Browser can only act as adapters or context providers; they don't change the Run Ledger + verifier + gate completion ruling.
- A CodeMap cache hit means "context reuse worked," not "the code is correct."

## What to absorb next

1. An AST / Tree-sitter-level artifact builder.
2. A real MCP client compatibility smoke (wiring into a concrete MCP host; today there's a stdio protocol smoke and a write smoke).
3. A safe `.worktreeinclude` copy strategy for worktrees.
4. A provider-neutral agent runtime adapter.

# CodeMap / Context Selection / Repo MCP / Agent Orchestration (v0.18.0)

This layer keeps absorbing the core ideas behind RepoPrompt CE: **build an auditable code map and select context first, then narrow repo operations into tools with permission boundaries, and finally run red-green mode under single-writer orchestration.**

On top of v0.14, v0.15 absorbed RepoPrompt CE's newer `content-addressed-codemaps` direction: the CodeMap is no longer a throwaway scan on every run — it has a **content-addressed artifact cache**. v0.15 also ships a **read-only live stdio JSON-RPC MCP skeleton**, so repo reads can actually be driven from the tool layer.

v0.16 nailed down the build-vs-buy boundary: red-green-mode maintains its own first-party thin server as the real MCP core. v0.17 used `rgm_mcp_adapters.py` to register external MCP servers — RepoPrompt CE, GitHub, Browser, Filesystem, Docs — as adapter-only, and added a host preflight. v0.18 adds `repo.patch.apply` to the first-party server, but it is **write-gated**: it stays out of `tools/list` by default and appears only under `RGM_MCP_ENABLE_WRITE=1`. Calls still have to clear the Run Ledger plus `PLAN_ONE_FIX`/hypothesis plus the Context Pack selected/risk/dependency boundary. External MCP servers supply context only; they never stand in for the Run Ledger, the verifier, or the gate.

None of it is proof of completion. Completion is decided by the external verifier plus `rgm_gate.py`, and nothing else.

## 1. Content-addressed CodeMap: repo skeleton map + reusable artifact cache

```bash
python3 tools/rgm_codemap.py build --root . --out-dir .rgm
```

Output:

- `.rgm/codemap.json`
- `.rgm/codemap.md`
- `.rgm/codemap-artifacts/<sha-prefix>/<artifact-key>.json`

Key fields:

| Field | Meaning |
|---|---|
| `schema_version` | `rgm-codemap.v2` |
| `files` | Per text/code file: language, tokens, is-test flag, symbols, imports, dependencies, dependents, risk_tags, `content_sha256`, `artifact_key` |
| `symbol_index` | Symbol index over path + line + kind + name |
| `dependency_edges` | File-level dependency edges, e.g. `src/service.py → src/auth.py` |
| `test_links` | Heuristic links between test files and the files they cover |
| `cache` | Whether the content-addressed cache is on, plus hits/misses/writes and the pipeline hash |
| `pipeline_identity` | Artifact identity inputs: parser, version, max_file_bytes, and so on |
| `not_completion_proof` | Always true |

The artifact key is computed from:

```text
content_sha256 + language + pipeline_sha256 + artifact_schema_version
```

When neither file content nor the parser pipeline changes, the next build reuses the artifact; when either the content or the parser identity changes, the entry misses and gets rebuilt. That upgrades context engineering from "rescan everything each time" into infrastructure you can cache, invalidate, and audit.

Common flags:

```bash
python3 tools/rgm_codemap.py build --root . --out-dir .rgm --refresh-cache
python3 tools/rgm_codemap.py build --root . --out-dir .rgm --no-cache
python3 tools/rgm_codemap.py build --root . --out-dir .rgm --cache-dir /tmp/rgm-codemap-cache
```

## 2. Context Selection: the selection algorithm

```bash
python3 tools/rgm_codemap.py select \
  --root . \
  --task "<task description>" \
  --verify "<verify command>" \
  --out-dir .rgm
```

Selection algorithm `rgm-codemap-selection.v1`:

1. Score path / content / symbol / import / risk hits against the task terms.
2. Expand primary hits one hop along dependencies and dependents.
3. Pull in linked tests and the production files they cover.
4. Files passed with `--include` rank highest, but `../` escapes outside the root are rejected.
5. Truncate by score and token budget.

Output still lands in `.rgm/context-pack.json/md` under schema `rgm-context-pack.v2`, carrying:

- `codemap_path`
- `codemap_schema_version`
- `codemap_cache`
- `selection_algorithm`
- `dependency_edges`
- `test_links`

Ironclad rule: to edit anything outside `selected_files` / `risk_files` / `dependency_edges`, write the hypothesis and the reasoning into the ledger first.

## 3. First-party live MCP server (read-only by default; write tools need an explicit switch)

```bash
python3 tools/rgm_mcp_server.py
```

The script exposes read-only tools over stdio JSON-RPC and answers in the MCP `2025-11-25` shapes: `initialize` / `tools/list` / `tools/call` / `structuredContent`. On install, pin the repo root with `RGM_MCP_ROOT` so the host's cwd can't drift.

| tool | Behavior | Proves completion? |
|---|---|---|
| `repo.codemap.build` | Build the CodeMap plus content-addressed artifacts | No |
| `repo.context.select` | Generate a context pack | No |
| `repo.read.slice` | Read a file slice inside the selected/risk/dependency boundary | No |
| `repo.diff.summary` | Read a git diff summary | No |
| `repo.verify.run` | Disabled by default; runs the verifier only under `RGM_MCP_ALLOW_VERIFY=1` | No |
| `agent.explore.read_only` | Build a CodeMap, a context pack, and a risk/test/unknowns summary | No |
| `repo.patch.apply` | Hidden by default; under `RGM_MCP_ENABLE_WRITE=1`, applies a unified diff behind ledger/context/hypothesis guards and returns a diff summary | No |

**`repo.patch.apply` is not exposed by default.** Turning the write tool on is still not a license to write freely: the patch must be a unified diff; the call must carry a ledger path, a ledger current/next state compatible with `PLAN_ONE_FIX`, a ledger hypothesis, a call-level `hypothesis`, and a `context_pack`; and target files must come from selected_files / risk_files / dependency_edges. Deletes, renames, chmods, path escapes, and obvious test skip/xfail anti-cheat patterns are rejected. Code still gets written on exactly one lane — `red_green_driver` — and it still has to clear the verifier and the gate.

Install profile and smoke test:

```bash
python3 tools/rgm_mcp_profile.py strategy
python3 tools/rgm_mcp_profile.py config --root . --name rgm-readonly-repo
python3 tools/rgm_mcp_smoke.py --root . --context-call
python3 tools/rgm_mcp_smoke.py --root . --context-call --write-call
```

`mcp.host_status` returns `ownership_strategy=first_party_thin_server`, `write_tools_exposed_by_default=false`, and `completion_requires=[external verifier, rgm_gate.py]`. An unknown tool must come back as JSON-RPC `-32602`, not disguised as a tool execution failure.

## 4. Repo MCP manifest: wrapping repo operations in tool boundaries

```bash
python3 tools/rgm_repo_mcp.py manifest
```

Emits an MCP-ready manifest: `rgm-repo-mcp.v2`.

Tools fall into three classes:

| Class | Examples | Permissions |
|---|---|---|
| Read-only repo tools | `repo.codemap.build`, `repo.context.select`, `repo.read.slice`, `repo.diff.summary`, `repo.verify.run` | Read-only by default; may write audit artifacts such as `.rgm/`; can never prove completion |
| Write tools | `repo.patch.apply`, `repo.worktree.manage` | `repo.patch.apply` is hidden by default and needs `RGM_MCP_ENABLE_WRITE=1` plus a ledger hypothesis, `PLAN_ONE_FIX`, and the selected/risk/dependency boundary; deleting a worktree still needs a human gate |
| Agent tools | `agent.explore.read_only`, `agent.redgreen.driver`, `agent.gatekeeper` | Spells out which lane may write and which may only read |

The manifest records `live_server.protocol_version = "2025-11-25"`, the install profile command, the smoke command, the `RGM_MCP_ENABLE_WRITE=1` env gate for write tools, and `build_vs_buy_policy`. The live server exposes only the read-only subset by default; write tools enter `tools/list` solely behind the explicit env gate.

## 5. Agent orchestration: single writer, read/write isolation

```bash
python3 tools/rgm_repo_mcp.py plan \
  --task "<task>" \
  --runtime codex
```

Emits `rgm-agent-orchestration.v1`:

| lane | Role | Can write code? | Artifacts |
|---|---|---|---|
| `context_mapper` | Builds the CodeMap, context pack, and read slices | No | `.rgm/codemap.json`, `.rgm/context-pack.json/md`, explorer summary |
| `red_green_driver` | Sole writer; edits code against a hypothesis and runs the verifier | Yes | `.rgm-ledger.json`, diff |
| `gatekeeper` | Runs `rgm_gate.py`; returns PASS/FAIL only | No | `RGM_GATE=PASS|FAIL` |

The Codex runtime gets soft enforcement: you have to run the gate yourself before reporting `FULL_GREEN`. The Claude Code runtime is flagged as supporting a hard Stop hook.

## 6. Build-vs-buy decision

| Option | Verdict | Why |
|---|---|---|
| Build RGM's own first-party thin MCP server | ✅ Core path | Completion semantics, write boundaries, and the gate stay yours |
| Use RepoPrompt CE's MCP as the core | ❌ Not recommended | It serves its own app/workspace model; its releases, schema, and permission boundaries are outside RGM's control |
| Call other authors' MCP servers | ✅ Adapter only | Fine for GitHub/browser/search/filesystem context; never a completion proof |
| Official MCP SDK | ✅ Optional adapter | Pinning a stable version cuts hand-written protocol code; must not block the core red-green loop |

Details in `references/mcp-build-vs-buy-and-install.md`.

Before wiring up a real host config, run:

```bash
python3 tools/rgm_mcp_adapters.py host-check --config /path/to/mcp-config.json --root .
```

It must fail when the RGM first-party core is missing. Every external server is `adapter_only`, no exceptions.

## 7. Finishing discipline

- The CodeMap, content-addressed artifacts, context pack, and read-only explorer summary are not proof of completion.
- What `repo.patch.apply` returns is not proof either; it must return `not_completion_proof=true` plus a diff summary, and it never replaces the verifier or the gate.
- A passing `repo.verify.run` is still not final proof; you also need `rgm_gate.py` to print `RGM_GATE=PASS`.
- Keep a single writer in multi-agent setups. Writing code in parallel requires `rgm_partition.py check` first, plus git worktree isolation.
- Every tool and agent handoff hits disk. On a long-run cold start, trust files only — never chat history.

---

> The section below is copied verbatim from SKILL.md (v0.19.0 slimming pass — see the references index table in SKILL.md). It only moved; not a word of the body changed.

## CodeMap / Selection / Repo MCP / Agent Orchestration: turning it into callable capability

Context Pack v0.13 answered "what do I look at first." v0.14 added the repo skeleton map and tool boundaries. v0.15 absorbed RepoPrompt CE's newer content-addressed CodeMap direction, turning the CodeMap into context infrastructure you can **cache, invalidate, and audit**. v0.16 locked down the MCP build-vs-buy policy: the core is **your own first-party thin MCP server**. v0.17 pulled other authors' MCP servers into an adapter registry and a host preflight, guaranteeing they can only act as context providers. v0.18 turns on **write tools, but write-gated**: `repo.patch.apply` stays out of `tools/list` by default, appears only under `RGM_MCP_ENABLE_WRITE=1`, and still has to respect the ledger / hypothesis / context pack boundaries when called:

```bash
python3 tools/rgm_codemap.py build --root . --out-dir .rgm
python3 tools/rgm_codemap.py select --root . --task "<user task>" --verify "<verify command>" --out-dir .rgm
python3 tools/rgm_repo_mcp.py manifest
python3 tools/rgm_repo_mcp.py plan --task "<user task>" --runtime codex
python3 tools/rgm_mcp_server.py   # first-party stdio JSON-RPC: read-only repo tools + explorer lane
python3 tools/rgm_mcp_profile.py config --root .
python3 tools/rgm_mcp_smoke.py --root . --context-call
RGM_MCP_ENABLE_WRITE=1 python3 tools/rgm_mcp_server.py   # exposes repo.patch.apply only under ledger/context/hypothesis guards
python3 tools/rgm_mcp_smoke.py --root . --context-call --write-call
python3 tools/rgm_mcp_adapters.py registry
python3 tools/rgm_mcp_adapters.py plan --adapter repoprompt-ce --task "<user task>"
python3 tools/rgm_mcp_adapters.py host-check --config <mcp-config.json> --root .
```

What lands on disk:
- `.rgm/codemap.json/md` v2: files, languages, symbols, imports, dependency edges, test links, risk tags, `content_sha256`, `artifact_key`, cache hit/miss.
- `.rgm/codemap-artifacts/<sha>/<artifact>.json`: content-addressed CodeMap artifacts keyed by `content_sha256 + pipeline_sha256`.
- `.rgm/context-pack.json/md` v2: selected files picked by `rgm-codemap-selection.v1`, plus relevant symbols, dependency edges, test links, verify commands, cache summary, and unknowns.
- `rgm-repo-mcp.v2` manifest: splits repo operations into read-only tools / write tools / agent tools and marks which tools are never a completion proof; also points at the live server command and the `RGM_MCP_ENABLE_WRITE=1` write-tool gate.
- `tools/rgm_mcp_server.py`: follows the MCP 2025-11-25 tools/list and tools/call shapes; by default exposes only `repo.codemap.build` / `repo.context.select` / `repo.read.slice` / `repo.diff.summary` / `repo.verify.run` / `agent.explore.read_only`; `repo.patch.apply` joins `tools/list` only under `RGM_MCP_ENABLE_WRITE=1`.
- `tools/rgm_mcp_profile.py`: generates the `mcpServers` config and prints the "first-party core / external adapter" policy.
- `tools/rgm_mcp_smoke.py`: protocol smoke covering `initialize` / `notifications/initialized` / `ping` / `tools/list` / `tools/call`.
- `tools/rgm_mcp_adapters.py`: external MCP adapter registry, adapter use plans, and host config preflight; explicitly demotes RepoPrompt CE, GitHub, Browser/Search, Filesystem, Docs SSOT, and friends to `adapter_only_not_completion_proof`.
- `rgm-agent-orchestration.v1` plan: `context_mapper` reads only, `red_green_driver` is the sole writer, `gatekeeper` runs the gate and nothing else.

Ironclad rules:
- The CodeMap, content-addressed artifacts, Selection, MCP manifest, and read-only explorer summary are all **inputs and orchestration artifacts** — not proof of completion.
- Only one lane writes code: `red_green_driver`. Every other agent may read, map, select context, and summarize.
- The live MCP server is **read-only** by default; `repo.patch.apply` is absent from the default tool list and shows up only under `RGM_MCP_ENABLE_WRITE=1`.
- Real MCP means a **first-party thin server**. Reference and adapt the official MCP protocol/SDK freely, but external MCP servers — RepoPrompt CE, GitHub, Browser — must first clear `rgm_mcp_adapters.py` registry/plan/host-check and may act only as adapters; they never replace the ledger, the verifier, or the gate.
- Write tools like `repo.patch.apply` need a ledger path plus a ledger hypothesis, must sit at or point to `PLAN_ONE_FIX`, must carry a non-empty call `hypothesis`, and must not cross the `context_pack` selected/risk/dependency boundary; path escapes, deletes, renames, chmods, test skips, and similar anti-cheat patterns are rejected.
- Write tools return a diff summary plus `not_completion_proof=true`. All they do is land a patch on the single-writer lane; they replace neither the verifier, the ledger, PRD coverage, nor `rgm_gate.py`.
- A passing `repo.verify.run` still isn't completion; `FULL_GREEN` waits for `rgm_gate.py` to print `RGM_GATE=PASS`.

Full how-to in `references/codemap-mcp-orchestration.md`; what v0.15 absorbed in `references/content-addressed-codemap-and-live-mcp.md`; MCP build-vs-buy and installation in `references/mcp-build-vs-buy-and-install.md`.

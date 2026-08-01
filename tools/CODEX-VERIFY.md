# Verify Red-Green Guardrail Portability in Codex (One Command)

> Background: the five enforcement teeth are pure Python CLIs adjudicated by exit code, so in theory they work across runtimes. But the **hard tooth, the Stop hook, only has an equivalent event in Claude Code**, and cold-start resumption in Codex has never been measured. This page turns "in theory" into "the exit code you actually measured in Codex."
> An agent running inside Claude Code cannot exercise the Codex runtime — so this step has to be run by a human, but it is only one command.

## How to run it

1. Open the red-green-mode skill directory in Codex (`~/.agents/skills/red-green-mode` or wherever it is symlinked).
2. Run:

```bash
bash tools/verify-codex-runtime.sh
```

3. Read the exit code and the report:
   - **exit 0** = all five teeth carry their exit codes over to Codex ([1] tool unit tests green, [2] ledger cold-start bootstrap reads state back, [3] gate --mutation correctly catches a dead target).
   - **exit 1** = some mechanical guardrail is dead in Codex — fix the environment for every ❌ in the report before running red-green there.

## Pay attention to section [4], the hard tooth

The script detects the runtime. Inside Codex it prints:

```
·  this runtime is not Claude Code -> **hard enforcement unavailable** (no Stop event to physically block wrapping up)
·  degraded to soft enforcement: before wrapping up the agent must run rgm_gate.py on its own, and only RGM_GATE=PASS allows reporting FULL_GREEN
·  -> this degradation is expected and does not count as a fail; but do not blindly trust 'all green' in this runtime (see VERSION-AND-STATE.md §5)
```

This degradation is **expected** and does not count as a failure. The conclusion: in Codex, mechanical enforcement is **soft discipline plus self-imposed exit-code checks**, with no hook behind you. **Do not trust "all green" in Codex just because a mechanical enforcement layer exists.** The full degradation matrix is in `VERSION-AND-STATE.md` §5.

## Want to verify cold-start resumption too (optional, manual)

Step [2] already verifies mechanically that `rgm_ledger bootstrap` reads state back from disk. To verify "resume in a new Codex session" end to end: run a long task → `init` plus a few rounds of `add` partway through → open a new Codex session → run `rgm_ledger bootstrap` and read `PROGRESS.md` → confirm it picks up from the cursor and does not redo work already done. The cost of this is unknown (whether a new Codex session is free has not been measured); once you have run it, add the conclusion next to the ownership rules in `VERSION-AND-STATE.md` §3.

## Where to record the result

Take the exit code plus the actual output of [4] and update the "Codex" column of the runtime degradation matrix in `VERSION-AND-STATE.md` §5 — change "unverified" to "measured, exit N @ <date>". That closes Codex equivalence from "known gap" to "verified fact."


## v0.12 Intake Router smoke

Right after a cold start in Codex / CodexAI, run:

```bash
python3 tools/rgm_intake.py --runtime codex --validator present --prompt "enter red-green mode and get pytest to all green"
```

Expect `should_enter_red_green=true` and `route_name=red_green`, with `runtime_policy.gate_enforcement` reminding you that Codex only has the soft tooth: you must run `rgm_gate.py` yourself, and only `RGM_GATE=PASS` allows reporting `FULL_GREEN`.

## v0.13 Context Pack smoke

Build the context pack before entering `PRECHECK`:

```bash
python3 tools/rgm_context_pack.py build \
  --root . \
  --task "enter red-green mode and get pytest to all green" \
  --verify "python3 -m pytest tests/ -q"
```

Expect the `<!--RGM_CONTEXT_PACK_BEGIN-->` sentinel on stdout and both `.rgm/context-pack.json` and `.rgm/context-pack.md` on disk; in the JSON, `not_completion_proof=true`, and `test_commands` contains the verifier you passed in. Note that this is only an input package — Codex still has to run `rgm_gate.py` and see `RGM_GATE=PASS` before wrapping up.

## v0.15 CodeMap / Repo MCP / Live MCP smoke

```bash
python3 tools/rgm_codemap.py build --root . --out-dir .rgm
python3 tools/rgm_codemap.py select \
  --root . \
  --task "enter red-green mode and get pytest to all green" \
  --verify "python3 -m pytest tests/ -q" \
  --out-dir .rgm
python3 tools/rgm_repo_mcp.py manifest
python3 tools/rgm_repo_mcp.py plan --task "enter red-green mode and get pytest to all green" --runtime codex
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
  | python3 tools/rgm_mcp_server.py
```

Expect: `rgm_codemap.py build` emits `<!--RGM_CODEMAP_BEGIN-->` and `select` emits `<!--RGM_CODEMAP_CONTEXT_BEGIN-->`; the CodeMap schema is `rgm-codemap.v2` and carries `CACHE_HITS/MISSES/WRITES`; the manifest schema is `rgm-repo-mcp.v2`, read-only by default, pointing at `tools/rgm_mcp_server.py`; the plan schema is `rgm-agent-orchestration.v1`, with the Codex runtime marked as soft-tooth; MCP `tools/list` includes `repo.codemap.build` and `agent.explore.read_only`, and does not expose `repo.patch.apply` by default. Note that these are all input and orchestration artifacts; you still cannot report `FULL_GREEN` until `rgm_gate.py` prints `RGM_GATE=PASS`.

## v0.16 First-party MCP profile / protocol smoke

How real MCP is meant to land here: red-green-mode's own thin stdio server carries the core, and MCP servers written by others act only as adapters or context providers. They never replace the ledger, the verifier, or the gate.

```bash
python3 tools/rgm_mcp_profile.py strategy
python3 tools/rgm_mcp_profile.py config --root . --name rgm-readonly-repo
python3 tools/rgm_mcp_smoke.py --root . --task "enter red-green mode and get pytest to all green" --context-call
```

Expect: `strategy` prints `recommendation=build_first_party_thin_mcp_server`; `config` prints an `mcpServers` block you can drop into an MCP host, pinning the repo root via `RGM_MCP_ROOT`; `smoke` prints `MCP_SMOKE=PASS`, `PROTOCOL_VERSION=2025-11-25`, `OWNERSHIP_STRATEGY=first_party_thin_server`, and `WRITE_TOOLS_EXPOSED_BY_DEFAULT=false`. An unknown tool should return JSON-RPC `-32602`, and `repo.patch.apply` is not exposed by default.

Note that even a passing `repo.verify.run` is only a necessary condition; you still cannot report `FULL_GREEN` until `rgm_gate.py` prints `RGM_GATE=PASS`.

## v0.17 External MCP adapter registry / host preflight

If your MCP host also has external servers configured — RepoPrompt CE, GitHub, Browser/Search, Filesystem, Docs — run the adapter registry and the host check first:

```bash
python3 tools/rgm_mcp_adapters.py registry
python3 tools/rgm_mcp_adapters.py plan --adapter repoprompt-ce --task "enter red-green mode and get pytest to all green" --root .
python3 tools/rgm_mcp_adapters.py host-check --config /path/to/mcp-config.json --root .
```

Expect host-check to print `MCP_HOST_CHECK=PASS` with `rgm-readonly-repo` classified as `first_party_core` and every other external server classified as `adapter_only`. If the config is missing the RGM first-party core, `MCP_HOST_CHECK=FAIL` is the correct result. Never treat an external MCP such as RepoPrompt CE, GitHub, or Browser as the red-green core.

## v0.18 Write-gated MCP patch smoke

Write tools are not free writes. Run the protocol smoke first:

```bash
python3 tools/rgm_mcp_smoke.py --root . --task "write-gated MCP" --context-call --write-call
```

Expect the output to include:

```text
MCP_SMOKE=PASS
WRITE_TOOLS_EXPOSED_BY_DEFAULT=false
MCP_WRITE_SMOKE=PASS
WRITE_TOOL_ENV_GATE=PASS
PATCH_NOT_COMPLETION_PROOF=true
```

Inside a temporary repo, `--write-call` verifies that `tools/list` has no `repo.patch.apply` by default; that it appears only once `RGM_MCP_ENABLE_WRITE=1` is set; and that a successful patch must carry a ledger, a hypothesis, and a context_pack, and comes back with `not_completion_proof=true`. None of this substitutes for the full verifier or for `rgm_gate.py`.

To generate a real host config snippet, use the explicit switch:

```bash
python3 tools/rgm_mcp_profile.py config --root . --name rgm-write-gated-repo --enable-write
```

Confirm that `RGM_MCP_ENABLE_WRITE=1` shows up in the env only when you asked for it. The default profile still exposes no write tools.

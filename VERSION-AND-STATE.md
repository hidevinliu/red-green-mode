# red-green-mode · Version Truth & State Consolidation (Single Source of Truth)

> This page answers two questions the scattered docs never could: **(1) Which capabilities actually shipped, and which are still proposals? (2) Those state files and stop conditions — which is which, and which one governs which?**
> Maintenance rule: when you cut a release or add a capability, **edit only this page**; every other spec points here instead of restating versions on its own.
> Last updated: 2026-08-20 UTC (added the soft minimality/scope review inspired by Ponytail's YAGNI/stdlib-first discipline, v0.20.0; no new mechanical gate or write tool).

---

## 1 · Version truth table (what actually runs in the repo)

The criterion is whether the repo contains the tool and the git commit — not what some spec claims.

| Capability | Status | Where it lives (tool / evidence) | Introduced |
|---|---|---|---|
| Single-agent red-green (core loop + 4 ironclad rules) | ✅ Shipped | `SKILL.md` | v0.6.x baseline |
| PRD completeness gate (green ≠ done, for a spec) | ✅ Shipped | `SKILL.md` PRD gate | `11a3b19` |
| Computable stalling detection (stall-check exit code, wall clock off by default) | ✅ Shipped | `rgm_ledger.py stall-check` | `cdead42` |
| **Long-run mode** (context never overflows the window; resume from checkpoints) | ✅ **Shipped** | `rgm_window.py` / `run_longrun.sh` / `rgm_ledger.py` (`--mode longrun`/`bootstrap`/`resume`/`context_checkpoints`) | **v0.7.0 `8bd428c`** + `ee11d4d` |
| **Parallel work on partitionable tasks** (worktree isolation + decomposition gate) | ✅ **Shipped** | `rgm_partition.py` / `rgm_ledger.py` (`parallel`) | **v0.7.0 `8bd428c`** |
| Mechanical enforcement layer (4 teeth: anti-cheat / constraints / contract / aggregate gate + hard-tooth hook) | ✅ Shipped | `rgm_anticheat.py` `rgm_constraints.py` `acceptance_contract.py` `rgm_gate.py` `rgm_stop_hook.sh` | v0.10.0 `180d1c6` |
| Fixes from the four-way expert review (2× P0 + several P1) | ✅ Shipped (current HEAD `9f8e60e`) | see the negative red tests under `tests/` | v0.10.1 `8653bd0` |
| **Contract quality gate** (5th tooth: mutation testing catches dead targets + explain/restore + uniform sampling + `file.py::func` symbol-level targets) | ✅ Shipped | `rgm_mutation.py` / `rgm_gate.py --mutation` / `tests/test_mutation.py` / `contracts/contract-quality-gate.json` (dogfood 6/6 ALIVE) | v0.10.2 |
| **Contract quality gate upgrades**: stronger operators (arithmetic +/-*//) + multi-language seams (C family is best-effort, see LIMITATION) + `check-pair` ad-hoc entry point + Codex runtime self-check script | ✅ Shipped | `rgm_mutation.py` (lang-aware operators / `check-pair`) / `tools/verify-codex-runtime.sh` / `tests/test_mutation.py` (21 tests) | v0.11.0 |
| **Standalone `mutation-check` skill** (the contract quality gate packaged on its own, so you can hunt dead/weak tests outside red-green mode) | ✅ Shipped | `~/.agents/skills/mutation-check/` (symlinked into `~/.claude`, reuses this skill's `rgm_mutation.py`) | v0.11.0 |
| **Target scope + hard gates declared up front + stopping ≠ dumping a menu** (cures the root cause of "milestone goes green, then stop and ask what's next" + aligns with the operator autonomy policy) | ✅ Shipped | `acceptance_contract.py` (optional top-level `scope`/`next_milestone` + per-item `gate{kind,question}` validation; `ready` lists `gate` items separately under `gates[]`, counts them as neither ready nor blocked, and never calls it stuck when only gates remain) / `tests/test_contract.py` (+10 negative red tests) / `SKILL.md` (REPORT "stopping ≠ dumping a menu" autonomy-policy alignment + danger signals + contract field docs) | v0.11.1 |
| **Intake Router / Skill Matcher / Runtime Router** (architecture absorbed from a screenshot: route first, enter red-green second; Codex soft teeth and Claude Code hard teeth degrade isomorphically) | ✅ Shipped | `rgm_intake.py` / `tests/test_intake.py` / `references/intake-router-and-runtime.md` / `SKILL.md` INTAKE state | v0.12.0 |
| **Context Pack Gate** (RepoPrompt-style context engineering: produce selected files / symbols / verifier commands / risk / unknowns first, then enter PRECHECK; a context pack is not proof of completion) | ✅ Shipped | `rgm_context_pack.py` / `tests/test_context_pack.py` / `references/context-pack-gate.md` / `SKILL.md` CONTEXT_PACK state / `rgm_ledger.py` CONTEXT_PACK state | v0.13.0 |
| **CodeMap / Context Selection / Repo MCP / Agent Orchestration** (RepoPrompt-style CodeMap: symbols / dependencies / test links / risk tags; selection algorithm: task terms + symbols + dependency hop + linked tests; MCP-ready repo tool manifest; single-writer orchestration plan) | ✅ Shipped | `rgm_codemap.py` / `rgm_repo_mcp.py` / `tests/test_codemap.py` / `tests/test_repo_mcp.py` / `references/codemap-mcp-orchestration.md` / `SKILL.md` CodeMap/MCP section | v0.14.0 |
| **Content-addressed CodeMap cache + live read-only MCP server** (CodeMap artifacts are addressed by content hash + pipeline hash; cache hits/misses/writes are auditable; stdio JSON-RPC exposes read-only repo tools plus `agent.explore.read_only`, and no write tools by default) | ✅ Shipped | `rgm_codemap.py` (`rgm-codemap.v2` / `.rgm/codemap-artifacts/`) / `rgm_mcp_server.py` / `rgm_repo_mcp.py` (`rgm-repo-mcp.v2` live_server) / `tests/test_codemap.py` / `tests/test_mcp_server.py` / `references/content-addressed-codemap-and-live-mcp.md` | v0.15.0 |
| **First-party MCP build-vs-buy + install/smoke** (the real MCP core is maintained by red-green-mode's own thin server; external MCPs serve only as adapters / context providers; generates `mcpServers` config and validates `initialize`/`tools/list`/`tools/call`/read-only boundary with a protocol smoke test) | ✅ Shipped | `rgm_mcp_server.py` (MCP `2025-11-25`/`structuredContent`/`mcp.host_status`/`RGM_MCP_ROOT`) / `rgm_mcp_profile.py` / `rgm_mcp_smoke.py` / `rgm_repo_mcp.py` build-vs-buy policy / `tests/test_mcp_server.py` / `references/mcp-build-vs-buy-and-install.md` | v0.16.0 |
| **External MCP adapter registry + host preflight** (registers external MCPs such as RepoPrompt CE / GitHub / Browser / Search / Filesystem / Docs SSOT explicitly as adapter-only; a real host config must contain the RGM first-party core; an adapter plan may never serve as completion proof) | ✅ Shipped | `rgm_mcp_adapters.py` (registry/plan/host-check) / `rgm_repo_mcp.py` adapter_registry / `tests/test_mcp_adapters.py` / `references/mcp-build-vs-buy-and-install.md` / `evals` rgm-54 | v0.17.0 |
| **Write-gated MCP patch apply** (`repo.patch.apply` is hidden by default; it enters `tools/list` only when `RGM_MCP_ENABLE_WRITE=1`; each call requires a ledger + PLAN_ONE_FIX/hypothesis + context_pack selected/risk/dependency boundaries; returns a diff summary with `not_completion_proof=true`) | ✅ Shipped | `rgm_mcp_server.py` (`repo.patch.apply` / env gate / patch guard) / `rgm_mcp_smoke.py --write-call` / `rgm_repo_mcp.py` manifest / `tests/test_mcp_server.py` / `tests/test_repo_mcp.py` / `evals` rgm-55 | v0.18.0 |
| **Three-perspective audit remediation** (A1 stop-hook fails closed / A2 gate CONTRACT switches from denylist to allowlist / D1 atomic ledger writes / D2 verifier timeout / D3 corrupt load → exit 2 / B1 case-insensitive noqa / B2 parenthesized assert and non-zero-constant variants / C1 intake rejects cheating intent / C3 Chinese bigrams + low-signal unknown / D4 an empty prompt no longer blocks on stdin / F1·F2 doc consistency; the A4 delete-attestation bypass is folded into E1 and documented as a trust boundary, see ANTICHEAT-LIMITATIONS §15) | ✅ Shipped | `rgm_stop_hook.sh` / `rgm_gate.py` / `rgm_ledger.py` / `rgm_anticheat.py` / `rgm_intake.py` / `rgm_context_pack.py` + 19 red-first tests; GATE self-check `RGM_GATE=PASS` (VERIFIER+ANTICHEAT+CONSTRAINTS all green, 252 passed) | v0.18.1 |
| **Algorithm-layer hardening** (an adversarial stress test of the judgment layer, prompted by the question "does the algorithm actually hold up?"): C2 intake wraps short English validator words in `\b` word boundaries (CI/tsc/lint/spec no longer match decide/specification); C1 cheat filtering moves from one long regex to **triple co-occurrence** (tamper action × test object × turn-green goal), cutting measured misses from 5/8 to 0/8 while leaving legitimate delete / skip / fix-a-skipped-test cases untouched, and `reason` states honestly that the heuristic is not exhaustive and that the real defense is downstream anti-cheat (confidence 0.9 → 0.75); context_pack warns on docs-only low signal (when a Chinese task matches only docs/test files and no implementation code, it says so instead of pretending it picked right). **Every adversarial sample is written as a pytest** (coverage floor + word-boundary parametrize + docs-only), so miss rate and mis-selection rate become regressable exit codes | ✅ Shipped | `rgm_intake.py` (`_cheat_intent` triples + `\b` word boundaries) / `rgm_context_pack.py` (`_is_code_file` + docs-only) + 8 adversarial tests; GATE self-check `RGM_GATE=PASS` (260 passed) | v0.18.2 |
| **Red-signal construction gate + wide-refactor expand-contract + tautological false green** (ported after closely reading the actual code in `mattpocock/skills`: upgrades the weak handoff "precondition 1: no verifier → switch to TDD" into `diagnosing-bugs` Phase 1's "constructing a red light is the highest-leverage step" — a 10-rung ladder of construction techniques + a tightening method + four completion criteria; `to-tickets`'s wide-refactor expand-contract covers the "a vertical slice can't reach green" edge; `tdd`'s tautological assertions add a false-green category) | ✅ Shipped (prose gate, no new .py tooling, but covered by eval regression) | `SKILL.md` (PRECHECK preconditions 1/2 + red-signal construction gate + Matt Bridge wide-refactor + danger signals) / `references/red-signal-construction.md` / `evals/evals.json` scenario **rgm-56** (README/SKILL count 51→52, locked down by the F1 consistency test) / full suite of 262 tests + `RGM_GATE=PASS` | v0.19.0 |
| **Soft minimality/scope review** (Ponytail-inspired YAGNI → reuse → stdlib/native → smallest correct diff; deliberate simplifications carry a ceiling and revisit trigger; final diff review uses `delete` / `stdlib` / `native` / `yagni` / `shrink`) | ✅ Shipped (prose/reference only; deliberately not a mechanical tooth or line-count target) | `SKILL.md` / `references/minimality-and-scope.md` / `references/state-machine-and-ledgers.md` / `references/ironclad-rules-and-reporting.md` / `evals/evals.json` scenario **rgm-57** | v0.20.0 |

> ⚠️ **Correcting a long-standing misreading**: long-run and parallel mode are **not** "optional proposals awaiting sign-off" — they landed in the repo back in v0.7.0. The line "add two optional outer modes (pending confirmation)" in an earlier design note was **proposal-era phrasing**; it is now a done deal. "Optional" means **off by default** (pass no flag and you get a single agent), not "not built yet".
>
> ⚠️ **SKILL.md has been slimmed down to a skeleton (doc refactor, no behavior change)**: `SKILL.md` now keeps only the frontmatter, "what this is / when to use it", the hard mechanisms (invocation and exit codes for `rgm_gate.py` / `rgm_anticheat.py` / `rgm_mutation.py` / `acceptance_contract.py` / `rgm_ledger.py stall-check` / `rgm_partition.py`), the state machine's advance order, the 9-item ironclad rules checklist, and the references index table. Where the table above says "lives in some section of `SKILL.md`", **that prose now sits under `references/`**, moved over without changing a word:
> - Theory foundations / preconditions before you start → `references/preflight-and-theory.md`
> - Per-state exit conditions / Run Ledger / Checkpoint / Quality Score / decision table / ledger commands → `references/state-machine-and-ledgers.md`
> - Red-light classification · ENV_RED subtypes · triage / inner and outer loops / check pipeline / hypothesis gate → `references/red-triage-and-loops.md`
> - Full text of the 9 ironclad rules / danger signals / closing report / stopping ≠ dumping a menu → `references/ironclad-rules-and-reporting.md`
> - Business rule discovery gate / online evidence rules / Evidence Ledger → `references/business-rule-discovery-gate.md`
> - PRD completeness gate / Matt Pocock Bridge (including wide-refactor expand-migrate-contract) → `references/mattpocock-bridge.md`
> - Intake Router / Context Pack / CodeMap·MCP / long-run·parallel / maintenance regression → their existing `references/*.md` files (the former SKILL.md passages were appended to the end of each file)
>
> ⚠️ **There are no git tags**: version numbers live scattered across commit messages and SKILL.md (SKILL.md even mixes three of them: `v0.6.2`/`v0.10.0`/`v0.10.1`). Version truth is inherently fuzzy. **Recommendation**: tag releases with `git tag v0.10.x` so the "Introduced" column in this table has something to point at.

---

## 2 · State file vocabulary map (not three systems — one system plus an aliased repackaging)

Reviewers worried that "`.rgm-ledger.json` / `PROGRESS.md` / `.contextwarden/` are three unaligned state vocabularies". After checking: **there is only one real state system**, and the other name is the same discipline wrapped for a different runtime.

| Name | What it is | Who writes / who reads | Relationship to the others |
|---|---|---|---|
| **`.rgm-ledger.json`** | **Machine state SSOT (the only true runtime state)** | written by `rgm_ledger.py`; read by `bootstrap`/`stall-check`/`rgm_gate` | The sole authority. On a cold start, trust nothing else |
| **`PROGRESS.md`** | Human-readable handoff (the "human face" of the same system) | written by the agent; read by a human or agent on cold start | The ledger's **human-readable companion**, not a second system; template at `templates/PROGRESS.md` |
| `.contextwarden/state.json` and friends | Vocabulary of the **Codex `/goal` instruction template** | lives only in a separate Codex `/goal` instruction template, outside this repo | The same **ContextWarden discipline** repackaged for the Codex runtime; inside this skill, the equivalents are the two rows above. Where "ContextWarden" appears in `rgm_ledger.py` it is **just a comment** (naming the discipline) — it never reads or writes `.contextwarden/` |

In one sentence: **the skill's true state in any runtime is `.rgm-ledger.json` + `PROGRESS.md`; intake routing state is the route/mode/gates emitted by `rgm_intake.py`, which may go into a report but never replaces the ledger; the context input pack is `.rgm/context-pack.json`/`.rgm/context-pack.md`, the CodeMap input pack is `.rgm/codemap.json`/`.rgm/codemap.md` + `.rgm/codemap-artifacts/`, the live MCP server is `tools/rgm_mcp_server.py` (read-only by default; since v0.18 `repo.patch.apply` is exposed only under `RGM_MCP_ENABLE_WRITE=1` and is guarded by ledger/context/hypothesis checks), and the repo MCP / orchestration manifest is the permission contract between tools and agent lanes; all of them tell you what to look at first, who may read and write, and how to hand off — none of them replaces the ledger, the verifier, or the gate.** Seeing `.contextwarden/` does not mean a second state system exists — that is what the Codex goal template calls this same discipline.

---

## 3 · Where stop conditions belong (two loop layers, orthogonal, not in conflict)

Reviewers worried about "two budgets: `loop_supervisor.sh`'s versus ironclad rule 2's stall-check — which governs which?" After checking: **they sit at different layers, they are orthogonal, and the outer layer treats the inner one as a single `act`.**

```
Outer loop (decides "open another session? total rounds? total wall clock?")
  ├── tools/run_longrun.sh        inside the skill; wraps headless `claude -p` in a while loop;
  │                               branch isolation + hard budget  → unattended on CC
  └── an external loop_supervisor.sh          outside the skill; task-agnostic bash supervisor;
        ships its own max_rounds / max_no_progress / max_consecutive_failures / budget_seconds
        → any task that can expose an is_done/act command pair can wrap red-green as one act
        │
        ▼ each call treats "one full red-green session" as a single act
Inner loop (within one red-green session: "should this session stop?")
  └── rgm_ledger.py stall-check (no-progress exit code, the primary judge)
      + ironclad rule 2 quick calls (same error patched 3 times in a row / hypotheses keep failing)
      + wall clock off by default (opt-in)
```

**Ownership rules (single authority)**:
- **The inner budget is always decided by `stall-check`** — it governs "is this session still setting new lows, and should it stop?" No outer layer reaches past it to manage session internals.
- **The outer loop governs "start another session or not, how many total, total wall clock"** — it treats each red-green session as a black-box `act` and **does not re-count** the inner layer's red-light trend.
- Pick **one of the two outer loops per runtime**; don't stack them. Use `run_longrun.sh` for unattended CC headless runs; use an external supervisor script for general-purpose, cross-task, or repo-external orchestration.

---

## 4 · Truth annotations for the specs this page supersedes

The design notes below live outside this repo and still have value (design reasoning, acceptance scenarios), but **this page is authoritative on version and state**:

| Design note | Current status | Read it for |
|---|---|---|
| Mechanical-enforcement-layer / acceptance-contract-pipeline PRD | ✅ Landed in v0.10.0/.1 | Design and acceptance scenarios for the 4 teeth |
| Contract quality gate PRD | ✅ Shipped in v0.10.2 | Design and acceptance scenarios for the 5th tooth |
| Long-run / parallel upgrade proposal | ✅ Long-run + parallel **landed in v0.7.0** ("optional, needs confirmation" is stale phrasing) | Motivation for long-run/parallel and the distinction between the three checkpoint kinds |
| Context-governance `/goal` instruction template | Codex `/goal` packaging template (`.contextwarden` vocabulary); the in-skill equivalent is `rgm_ledger` + `PROGRESS.md` | How long runs are done in the Codex runtime |
| Loop-supervisor PRD | ✅ Implemented as an external `loop_supervisor.sh` (an **outer** loop, not a tool inside the skill) | Exit-code semantics of the general-purpose supervisor |

---

## 5 · Runtime degradation matrix (don't over-trust a weak runtime)

Some teeth in the mechanical enforcement layer are **runtime-conditional** — the hard teeth only bite in Claude Code, so don't treat `FULL_GREEN` from another runtime as equally strong evidence.

| Guardrail | Claude Code | Codex / other runtimes | What you fall back on |
|---|---|---|---|
| `rgm_anticheat` / `rgm_gate` / `acceptance_contract` / `rgm_constraints` / `rgm_mutation` (the **exit codes** of the 5 teeth) | ✅ Fully effective (pure Python CLI, cross-runtime) | ✅ Fully effective | Same as CC — these are plain scripts, and exit-code judgment doesn't care about the runtime |
| **Hard-tooth Stop hook** (`rgm_stop_hook.sh`: gate FAIL means exit 2, physically blocking the finish) | ✅ Effective (opt-in, wired into settings.json by hand) | ❌ **Not effective** (no equivalent Stop event, and Codex cold-start equivalence is unverified) | **Soft tooth**: in the GATE state the agent runs `rgm_gate.py` on its own initiative and only reports FULL_GREEN on `RGM_GATE=PASS`, backed by prompt discipline |
| A hook that guards test files and protected config (blocks test edits / config changes) | ✅ Hard hook backstop | ❌ May be absent (possibly even a full-access sandbox) | This rule is followed **voluntarily**: stop and confirm before deleting, sending anything outward, or changing config |

**In one sentence**: the exit codes of the 5 teeth work everywhere; **only CC's hard teeth can physically block you**. On Codex and similar runtimes, mechanical enforcement **degrades to "soft discipline + voluntary exit-code checks"** — the agent still has to run the gate itself, but with no hook backstopping it, restraint matters more. **Don't let "there is a mechanical enforcement layer" turn into blind faith in green outside CC.**

**Measured Codex run (2026-06-24, `verify-codex-runtime.sh` run on Codex)**: runtime = system `/usr/bin/python3`, **Python 3.9.6**. Results — **[2] ledger cold start ✅ and [3] gate --mutation caught a dead target ✅** (the core mechanism ports over; the pure-stdlib tools run fine on 3.9.6); **[1] tool unit tests failed because the system python has no pytest (ENV_DEPENDENCY, not a code incompatibility)** — every tool and test carries `from __future__ import annotations` and uses no 3.10+ runtime features, so installing pytest (`pip install pytest`) or using a python that already has it (`PYTHON=python3.x bash tools/verify-codex-runtime.sh`) runs the full suite; **[4] the hard-tooth Stop hook is unavailable (expected degradation, soft tooth covers it)**. **Conclusion: core exit-code judgment ports to Codex; treat the hard teeth as degraded; the full unit test suite needs pytest installed on Codex.** (The script has been hardened — next time it will explicitly classify "pytest not installed" as ENV_DEPENDENCY instead of falsely reporting "the tools are unreliable".)

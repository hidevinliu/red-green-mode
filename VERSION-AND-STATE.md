# red-green-mode · State & Stop-Condition Rules (Single Source of Truth)

> This page answers: which state file is which, which loop layer owns which stop condition, and which guardrails weaken outside Claude Code.
> Release history, the version truth table and the status of superseded design notes live in `CHANGELOG.md`. Section numbers are kept stable because other docs cite §2, §3 and §5.

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

## 5 · Runtime degradation matrix (don't over-trust a weak runtime)

Some teeth in the mechanical enforcement layer are **runtime-conditional** — the hard teeth only bite in Claude Code, so don't treat `FULL_GREEN` from another runtime as equally strong evidence.

| Guardrail | Claude Code | Codex / other runtimes | What you fall back on |
|---|---|---|---|
| `rgm_anticheat` / `rgm_gate` / `acceptance_contract` / `rgm_constraints` / `rgm_mutation` (the **exit codes** of the 5 teeth) | ✅ Fully effective (pure Python CLI, cross-runtime) | ✅ Fully effective | Same as CC — these are plain scripts, and exit-code judgment doesn't care about the runtime |
| **Hard-tooth Stop hook** (`rgm_stop_hook.sh`: gate FAIL means exit 2, physically blocking the finish) | ✅ Effective (opt-in, wired into settings.json by hand) | ❌ **Not effective** (no equivalent Stop event, and Codex cold-start equivalence is unverified) | **Soft tooth**: in the GATE state the agent runs `rgm_gate.py` on its own initiative and only reports FULL_GREEN on `RGM_GATE=PASS`, backed by prompt discipline |
| A hook that guards test files and protected config (blocks test edits / config changes) | ✅ Hard hook backstop | ❌ May be absent (possibly even a full-access sandbox) | This rule is followed **voluntarily**: stop and confirm before deleting, sending anything outward, or changing config |

**In one sentence**: the exit codes of the 5 teeth work everywhere; **only CC's hard teeth can physically block you**. On Codex and similar runtimes, mechanical enforcement **degrades to "soft discipline + voluntary exit-code checks"** — the agent still has to run the gate itself, but with no hook backstopping it, restraint matters more. **Don't let "there is a mechanical enforcement layer" turn into blind faith in green outside CC.**

**Measured Codex run (2026-06-24, `verify-codex-runtime.sh` run on Codex)**: runtime = system `/usr/bin/python3`, **Python 3.9.6**. Results — **[2] ledger cold start ✅ and [3] gate --mutation caught a dead target ✅** (the core mechanism ports over; the pure-stdlib tools run fine on 3.9.6); **[1] tool unit tests failed because the system python has no pytest (ENV_DEPENDENCY, not a code incompatibility)** — every tool and test carries `from __future__ import annotations` and uses no 3.10+ runtime features, so installing pytest (`pip install pytest`) or using a python that already has it (`PYTHON=python3.x bash tools/verify-codex-runtime.sh`) runs the full suite; **[4] the hard-tooth Stop hook is unavailable (expected degradation, soft tooth covers it)**. **Conclusion: core exit-code judgment ports to Codex; treat the hard teeth as degraded; the full unit test suite needs pytest installed on Codex.** (The script has been hardened — next time it will explicitly classify "pytest not installed" as ENV_DEPENDENCY instead of falsely reporting "the tools are unreliable".)

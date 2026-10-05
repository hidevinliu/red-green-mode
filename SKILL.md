---
name: red-green-mode
version: 0.24.0
description: >-
  Autonomous test-fix loop: run an objective verifier (tests, type-check, lint, CI), fix what is
  red, re-run, and finish only when the verifier exit code is all green. Use when the user wants
  work driven to green without supervision, including long-run or parallel runs (红绿灯模式 /
  跑到全绿 / 把 CI 修到全绿). Not for subjective work with no verifier or one-shot edits; use
  test-driven-development to write tests and systematic-debugging to root-cause one bug.
---

# Red-Green Mode

## What this is / when to use it

An unattended **self-verification loop**: finish when the verifier is all green, stop and fix
whatever is red, then verify again.

> **The verifier's exit code decides right and wrong. The agent's opinion does not.**

- This mode governs **outer orchestration only** — when to stop, what counts as done, no cheating, always leave a way back.
- It does **not** teach you to diagnose a red (that's `systematic-debugging`) and does **not** write your tests (that's `test-driven-development`).
- Three preconditions, all required: ① an objective verifier that can tell right from wrong; ② a trustworthy referee (a red is genuinely red, a green can still catch bugs, a green is not tautological); ③ an unambiguous task. When ① is missing, **construct a red you can actually fix against** — do not just say "switch to TDD" and walk away.
- Money / people / contracts / SOPs / formulas → clear the business-rule discovery gate first. Task ships a PRD → clear the PRD-coverage gate before finishing ("tests green" ≠ "PRD delivered").
- **Single agent by default.** Long-run and task-parallel are optional outer modes; pass no flags and behaviour is unchanged.
- `VERSION-AND-STATE.md` is the single source of truth for state-file ownership, stop-condition layering and runtime degradation; release history is in `CHANGELOG.md`.

## Hard mechanisms (exit-code enforced — actually run these)

```bash
# ① First step of any run: create the ledger (mandatory, however small the task; it powers the
#    wrap-up timing report. Only exemption: no write permission)
python tools/rgm_ledger.py init --task "<task>" --workspace "$PWD" \
  --skill-version "red-green-mode v0.24.0" --verify "python -m pytest -q" --out .rgm-ledger.json
# ② Every verify round (including INNER_VERIFY): record the remaining red count as a number, then
#    let the script rule on stalling (patience defaults to 5 rounds)
python tools/rgm_ledger.py add --out .rgm-ledger.json --state OUTER_VERIFY --result "4 failed" --red-count 4
python tools/rgm_ledger.py stall-check --out .rgm-ledger.json    # 0=continue 1=stalling, stop now and report honestly
# ③ Anti-cheat: scan the diff for newly introduced skips / weakened assertions / deleted assertions
#    / ignores / mocking out the logic under test
python tools/rgm_anticheat.py scan --diff-file <diff>            # 0=clean 1=cheating 2=bad input; escape hatch `# rgm-allow: reason`
# ④ Acceptance contract (the SSOT across PRD → TDD → red-green): validate / SHA-256 lock the
#    verifiers / dependency-aware ready ordering
python tools/acceptance_contract.py validate --file acceptance-contract.json      # 0=valid 1=invalid
python tools/acceptance_contract.py verify-attest --file acceptance-contract.json # 1=a verifier was swapped out
python tools/acceptance_contract.py ready --file acceptance-contract.json         # the loop may only stop at gates[]
# ⑤ Contract-quality gate: mutation testing catches dead targets that nothing can kill
#    (a target that loose means green proves nothing)
python tools/rgm_mutation.py check --contract acceptance-contract.json --root .   # 0=live target 1=dead target 2=bad input
# ⑤b Point-patch check: did the fix change the logic, or only the inputs the tests use? Records the
#    test's inputs to the target function, perturbs them, compares the old and new versions.
#    Run it when a fix to production code made a red test green.
python tools/rgm_pointpatch.py check --after <file.py> --base <rev> --root . --func <name> --record "<verifier>"  # 0=OK/INCONCLUSIVE 1=SUSPECT 2=cannot run
# ⑥ Partition gate (parallel mode only): exit 0 or no parallelism, and it refuses to split a
#    depends_on edge across two units
python tools/rgm_partition.py check --units units.json --contract acceptance-contract.json  # non-zero=fall back to a single agent
# ⑦ Wrap-up GATE state: the aggregate verdict (re-run verifiers / anti-cheat / contract coverage /
#    project constraints)
python tools/rgm_gate.py --ledger .rgm-ledger.json --diff-file <diff> --contract acceptance-contract.json --root .
#   add --mutation and --pointpatch-base <start rev> to run the two opt-in checks on every AC target
#   0 = sentinel RGM_GATE=PASS — the only credential that permits reporting FULL_GREEN
#   1 = any sub-check FAILed → go back to TRIAGE / PLAN_ONE_FIX, or report the gap honestly. FULL_GREEN is forbidden
#   2 = missing ledger / no --verify recorded → PRECHECK was incomplete. Go back and finish it;
#       do not claim BLOCKED_BY_INFRA
```

Hard enforcement (Claude Code, opt-in): `tools/rgm_stop_hook.sh` hooks the Stop event and exits 2
when the gate fails, physically blocking the finish (install: `tools/STOP-HOOK-INSTALL.md`). On
Codex there is only soft enforcement — the agent has to run the gate on its own discipline.
Anti-cheat is a heuristic seat belt; its boundaries are in `tools/ANTICHEAT-LIMITATIONS.md`.

## State machine order

```
INTAKE → CONTEXT_PACK → PRECHECK → BASELINE → TRIAGE → PLAN_ONE_FIX → INNER_VERIFY → OUTER_VERIFY → SMOKE_ASSERT → QUALITY_REVIEW → PRD_COVERAGE → GATE → REPORT
                                 ↘ BLOCKED_BY_INFRA / TEST_QUESTION / FLAKY / STOPPED_NO_PROGRESS ↗
```

- `PRECHECK` embeds sub-flows: the business-rule discovery gate `RULE_DISCOVERY → EVIDENCE_GATHERING → ASSERTION_DRAFT`; with a PRD, `PRD_PARSE → COVERAGE_MAP`, then `GAP_CHECK` before finishing.
- Front-loaded tools: `rgm_intake.py` (should this even enter red-green?) → `rgm_context_pack.py build` (look at the right things first) → `rgm_codemap.py build/select`. All three are **input artifacts, not proof of completion**.
- Before `PLAN_ONE_FIX`, run the soft **minimality/scope review** when the task could invite a new abstraction, dependency, wrapper, or broad refactor: check reuse → stdlib/native → smallest correct diff, and record any deliberate simplification's ceiling and revisit trigger. Read `references/minimality-and-scope.md`. This review can improve the plan or produce a finding; it is never a line-count target, a verifier, or a substitute for safety/tests.
- Pick exactly one completion state — "basically done" is not one of them: `FULL_GREEN` / `TASK_GREEN_WITH_BASELINE_RED` / `BLOCKED_BY_INFRA` / `STOPPED_NO_PROGRESS`.
- **Per-state exit conditions and failure routing → `references/state-machine-and-ledgers.md`.**

## Ironclad rules (9 — full text in `references/ironclad-rules-and-reporting.md`)

1. **Never fake green**: deleting / skipping / commenting out tests, weakening assertions, `type: ignore`, mocking out the failing logic — all forbidden. If you are convinced a test is wrong, **stop and ask the user**. Enforced by `rgm_anticheat.py`.
2. 🛑 **Stop conditions**: stalling is ruled on by `stall-check`'s exit code, not by eyeballing it. Also stop after 3 fixes for the same error, a run of failed hypotheses, or a regression in an already-green check.
3. 💾 **Checkpoint and stay revertible**: record the starting point, and **checkpoint on every all-green**. If you go off the rails, revert to the last green state — never stack changes on a dirty one.
4. ✅ **Baseline first, completion state last**: run everything once up front, record the baseline, label every red, then report one of the four states plus `before → after`.
5. 🎯 **Do not widen scope**: change only what is required to turn *this* task's reds green. Unrelated failures that were already red get tagged `BASELINE_RED`, listed separately, and left alone.
6. 🌀 **Quarantine flaky**: still flipping red and green after you rule out environment and isolation problems → quarantine and report it. Do not chase it, and definitely do not delete it.
7. 🚪 **Human gates still apply**: deletion, bulk moves, publishing externally and changing protected config still need user confirmation. "It was to get to green" is not a licence.
8. 🪟 **Long runs: externalise before you restart**: real state goes into the ledger / PROGRESS.md. The agent's job is to be cheap to lose, not to open a new window.
9. 🔀 **Parallel: never mutate a shared tree in parallel**: no partition gate, no parallelism. Merges are single-writer, and after merging you **re-verify the whole tree** before finishing.

## Where the detail lives (paths relative to `references/`)

| I want | Read this |
|------|--------|
| Why it is designed this way / the three preconditions in full / three flavours of fake green | `preflight-and-theory.md` |
| State exit conditions / Run Ledger / checkpoints / Quality Score / decision table | `state-machine-and-ledgers.md` |
| The 5 red categories + 8 `ENV_RED` sub-types / triage / inner and outer loops / hypothesis gate | `red-triage-and-loops.md` |
| Full ironclad rules / cheating-excuse crib sheet / warning signs / wrap-up report / stopping ≠ dumping a menu | `ironclad-rules-and-reporting.md` |
| How to construct a red when there are no tests or the bug will not reproduce (10 escalating options) | `red-signal-construction.md` |
| How to choose the smallest correct change and review over-engineering | `minimality-and-scope.md` |
| Business-rule discovery gate / rules for gathering evidence online / Evidence Ledger | `business-rule-discovery-gate.md` |
| Requirement-alignment bridge / expand→migrate→contract for wide refactors / PRD-coverage gate | `mattpocock-bridge.md` |
| Should this enter red-green at all / isomorphic degradation across Claude Code and Codex | `intake-router-and-runtime.md` |
| Context Pack fields and the usual misuses | `context-pack-gate.md` |
| CodeMap / context selection / repo MCP / single-writer orchestration | `codemap-mcp-orchestration.md` |
| Where the CodeMap cache ends and live MCP begins | `content-addressed-codemap-and-live-mcp.md` |
| MCP build-vs-buy / installation / external adapter registry | `mcp-build-vs-buy-and-install.md` |
| Full how-to for long runs and parallelism (externalised state / window sentinel / partition gate / merge gate) | `longrun-and-parallel.md` |
| Maintaining this skill: regression / self-test suite / eval scenario list | `maintenance-and-evals.md` |

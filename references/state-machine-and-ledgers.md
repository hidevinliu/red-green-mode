# Red-Green · Production Execution State Machine / Run Ledger / Checkpoint / Quality Score

> **When to read**: When you need each state's exit condition and failure route, the Run Ledger fields, the checkpoint policy, the post-green quality review score, the "can I change this?" decision table, or the full ledger tool commands. SKILL.md keeps only the state progression order.

> Everything below was moved verbatim out of SKILL.md (the v0.19.0 slim-down; see the references index table in SKILL.md). Only its location changed, not a word of the text.

## Production Execution State Machine

Run red-green mode as a state machine; don't improvise. Every time you enter the mode, advance through the states below in order, and keep a Run Ledger in your report. If the business-rule discovery gate fires, `PRECHECK` must first complete `RULE_DISCOVERY → EVIDENCE_GATHERING → ASSERTION_DRAFT`. With a PRD in play, `PRECHECK` must also complete `PRD_PARSE → COVERAGE_MAP`, and you must pass `PRD_COVERAGE` (`GAP_CHECK`) before you finish.

```
INTAKE → CONTEXT_PACK → PRECHECK → BASELINE → TRIAGE → PLAN_ONE_FIX → INNER_VERIFY → OUTER_VERIFY → SMOKE_ASSERT → QUALITY_REVIEW → PRD_COVERAGE → GATE → REPORT
                                 ↘ BLOCKED / TEST_QUESTION / FLAKY / STOPPED_NO_PROGRESS ↗
```

| State | Exit condition | Next |
|------|----------|------|
| `INTAKE` | `rgm_intake.py` or an equivalent judgment produces route/mode/gates; confirmed this is a verifiable autonomous task | `CONTEXT_PACK`; if this doesn't belong in red-green mode, switch to TDD/debug/research/doc-only instead of forcing the loop |
| `CONTEXT_PACK` | `rgm_context_pack.py build` has produced `.rgm/context-pack.json/md` listing selected files / symbols / test commands / risk files / unknowns; confirmed it is only an input artifact | `PRECHECK`; when there are no selected files, or unknowns expose a missing verifier / dirty tree / blown budget, handle it in `PRECHECK` first and never use the pack as proof of completion |
| `PRECHECK` | Verifier, runtime environment, and permission/workspace starting point located; if the business-rule discovery gate fired, the rule is extracted and at least one semantic assertion added; with a PRD, the acceptance checklist is extracted and mapped to verifiers | `BASELINE`; no verifier → `BLOCKED_BY_INFRA`; a rule or acceptance item without evidence → `TEST_QUESTION` |
| `BASELINE` | Full run finished and raw output recorded | `TRIAGE` |
| `TRIAGE` | Every red light labeled and clustered by shared root cause; if `STATE.md` exists, first run `loop_state.py recall "<current red-light signature>" --k 3` to recall lessons from similar past reds | Fixable → `PLAN_ONE_FIX`; not fixable → the matching stop state; on the way out you can run `loop_lesson_candidates.py STATE.md --threshold 2` to check whether a lesson should be promoted into a skill/guard/lint |

| `PLAN_ONE_FIX` | Hypothesis, prediction, and minimal change scope written down | `INNER_VERIFY` |
| `INNER_VERIFY` | Targeted verification of the current hypothesis | Green → `OUTER_VERIFY`; red → back to `PLAN_ONE_FIX` or stop |
| `OUTER_VERIFY` | Fast→slow full pipeline with no task-level regression | `SMOKE_ASSERT`; on regression, find the cause or roll back |
| `SMOKE_ASSERT` | At least one semantic assertion proves the thing actually works | `QUALITY_REVIEW`; a failure here means "all tests green but the semantics are wrong" (a test blind spot) → go back to `PLAN_ONE_FIX` and first add a targeted test that turns the semantic error into a real red light. **Do not go back to `TRIAGE`** — there is no red light to triage at that point |
| `QUALITY_REVIEW` | After green, review the change for quality, scope, boundaries, security, and maintainability | `REPORT` (no PRD) / `PRD_COVERAGE` (with PRD); when you find risk, add tests, fix it, or list it in the report |
| `PRD_COVERAGE` | (PRD only) Walk the acceptance list item by item: done / not covered / not done (`GAP_CHECK`) | `GATE`; any not-done or uncovered item → back to `TRIAGE`/`PLAN_ONE_FIX` and keep going, or report the gap honestly. **Do not report FULL_GREEN** |
| `GATE` | Run `tools/rgm_gate.py` for an aggregate verdict (re-run verifiers / anti-cheat / contract coverage / project constraints); only the `RGM_GATE=PASS` sentinel lets you through | `REPORT`; exit 1 (any sub-check FAIL) → back to `TRIAGE`/`PLAN_ONE_FIX` or report the gap honestly, **FULL_GREEN forbidden**; exit 2 → `BLOCKED_BY_INFRA` |
| `REPORT` | Completion state, before→after, remaining red lights, PRD coverage (with a PRD), GATE sentinel | Finish |

> **Mechanical enforcement layer (v0.10.0) — how the GATE state runs**: Finishing no longer rides on self-discipline. `rgm_gate.py` uses exit codes to settle "what makes you say it's done." Four sets of teeth, all under `tools/`:
> - `rgm_anticheat.py scan --diff-file <diff>`: scans the diff for newly introduced skips, weakened assertions, deleted assertions, silenced static checks, and mocked-out code under test. Exit 1 = cheating (ironclad rule 1 finally has a verifier). The escape hatch `# rgm-allow: reason` lets a change through while leaving an audit trail.
> - `rgm_constraints.py check --root <repo> --diff-file <diff>`: extracts read-only paths and protected-config constraints from CLAUDE.md/AGENTS.md; a violation exits 1. Wording-level constraints only reach the human-read checklist and never auto-FAIL.
> - `acceptance_contract.py {validate,attest,verify-attest,ready}`: validates `acceptance-contract.json` (the SSOT shared across PRD → TDD → red-green), SHA-256-locks verifier commands so they can't be swapped out, and orders depends_on into ready/blocked.
>   - **Optional `scope` / `next_milestone` (top level)**: `scope: "project"` means this contract *is* the definition of done for the whole project — all green = genuinely finished. `scope: "milestone-1/3"` plus `next_milestone: "<the next chunk>"` means that once it goes green you advance to the next milestone automatically and **may not** stop to ask "which one next?". A missing `scope` is backward compatible (treated as project level); a milestone without `next_milestone` gets a WARN.
>   - **Optional `gate` (item level)**: `{"kind": <one of the seven gate kinds>, "question": "<the factual question to ask when stopping at this gate>"}`. `kind` ∈ `cross-repo-runtime / destructive / protected-config / external-send / privacy-archive / append-only-ledger / target-confirm`. `ready` lists items carrying a `gate` separately under `gates[]` and excludes them from `ready`/`blocked`. The loop finishes every non-gate item on its own and **may only stop at `gates`**; when it stops, it reports the `question` (no menu — see "stopping ≠ dumping a menu"). When only gates remain, `ready --no-progress` does not call it stuck: stopping at the gate to ask a question is legitimate, not stalling.
> - `rgm_gate.py --ledger .rgm-ledger.json --diff-file <diff> [--contract …] [--root …]`: aggregates the four checks above into the machine-readable sentinel `<!--RGM_GATE_BEGIN--> RGM_GATE=PASS|FAIL …`. **Only `RGM_GATE=PASS` permits reporting `FULL_GREEN`.**
> - Hard teeth (opt-in, Claude Code only): `tools/rgm_stop_hook.sh` hooks the Stop event and exits 2 to block finishing when the gate FAILs. Installation lives in `tools/STOP-HOOK-INSTALL.md`; it **does not edit settings.json for you** (that would be a protected-config change — you wire it up by hand). On the Codex side you get soft teeth: calling rgm_gate yourself in the GATE state, plus the prompt.
> - ⚠️ **Honest limits**: this layer is a heuristic seatbelt, not taint analysis. Semantic-level weakening, aliased skips, cross-line strings, and production code edited to please the test all slip past it, and wording constraints only reach the human-read checklist. The full "what it can't catch" list is in `tools/ANTICHEAT-LIMITATIONS.md`.
>
> **v0.10.1 process revisions (after the four-way review)**:
> - **The GATE state runs contract verifiers for real by default**: when an `acceptance-contract.json` exists, `rgm_gate.py` defaults to `--run-contract-verifiers` instead of taking a hand-written `status:green` on faith. Use `--trust-contract-status` (weak) to go back to trusting it. GATE's CONTRACT sub-check **absorbs** PRD_COVERAGE's GAP_CHECK — don't judge it by hand in PRD_COVERAGE and again by machine in GATE; GATE wins.
> - **Feed the inner loop into stall-check too**: `INNER_VERIFY` rounds must also call `rgm_ledger.py add --red-count N` (how many reds the current targeted run has left). Otherwise, while you oscillate between `INNER_VERIFY` and `PLAN_ONE_FIX`, stall-check has no data and never fires. The inner loop is not a referee-free zone.
> - **Escape-hatch red line**: when `# rgm-allow` covers a **test-semantics change** — a skip, a deleted assertion, a weakened assertion — the reason **must** cite a confirmed `TEST_QUESTION` (ironclad rule 1: the agent does not get to decide on its own whether a test is right). Waving something through with no confirmation is cheating.
> - **Exception states return to the main flow**: once the user or the dependency resolves it, `TEST_QUESTION`/`BLOCKED_BY_INFRA` go back to `PRECHECK` to re-confirm the preconditions before re-entering the loop (on a cold start, trust only the ledger).
> - **GATE exit 2 ≠ real infra blockage**: if you exit 2 because `--verify` was never recorded or the ledger is missing, PRECHECK was incomplete → go back to `PRECHECK` and fill it in. Don't dress it up as `BLOCKED_BY_INFRA` and claim the environment blocked you.
> - **Parallel × contract**: acceptance items with dependencies must land in the same unit — `rgm_partition.py check --contract <contract>` refuses to split a `depends_on` edge. The contract SSOT stays in the shared base, workers report status only, and a single writer reconciles at merge time.

### Run Ledger Template

Keep at least this table every round (compress it if you like, but no field may go missing):

| Round | State | Command | Label | Hypothesis | Change | Result | Red# | Next |
|------:|-------|---------|-------|------------|--------|--------|-----:|------|
| 0 | BASELINE | `<cmd>` | `TASK_RED/ENV_RED/...` | - | - | `<summary>` | `4` | TRIAGE |
| 1 | INNER_VERIFY | `<targeted cmd>` | `TASK_RED` | `<why>` | `<files>` | pass/fail | `3` | OUTER/PLAN |

`Red#` = how many red lights are left this round; write it into the ledger with `--red-count N`. It is the only data source stall-check uses to compute the stalling trend (see ironclad rule 2). Non-verification rounds such as `PLAN_ONE_FIX` can leave it blank — stall-check skips them automatically. This table prevents three classes of accident: (1) the agent forgets which red light is an unrelated baseline; (2) after several rounds it starts treating guesses as facts; (3) it spins in place and nobody notices (Red# stops dropping → stall-check's exit code calls a halt).

### Checkpoint Policy: escape route first, autonomy second

The red-green loop must be rollbackable. In `PRECHECK`, determine the workspace type and establish a starting point. Before `PLAN_ONE_FIX`, record this round's touched files. When `OUTER_VERIFY` finds that something already green has regressed, roll back this round's changes first — don't keep stacking fixes on a dirty state.

| Scenario | Starting point | Per round | Rollback |
|------|----------|----------|----------|
| Git, clean | Record `git rev-parse HEAD` and `git status --short` | Ledger records `git diff --name-only` / touched files | `git restore <files>`, or return to the starting commit (subject to user permission) |
| Git, dirty | Don't mix in changes automatically; record the files that are already dirty | Touch only the files the task requires; stay off the user's changes | Stop if you would overwrite the user's changes |
| Non-Git / temp directory | Copy the files you're about to change with `tools/rgm_checkpoint.py create --name baseline --paths ...` | Checkpoint the touched files every round | `tools/rgm_checkpoint.py restore --name <name>` |

**A checkpoint is not permission to commit**: never commit or push on your own. Commits, deletions, migrations, and releases all stay behind the human gate.

### Post-green quality review gate + Quality Score

`FULL_GREEN` only proves "the referees you have didn't catch anything." It is not automatically production quality. After `SMOKE_ASSERT`, do one lightweight code review and give a Quality Score out of 10.

| Dimension | Points | Questions to ask |
|------|------|----------|
| Scope control | 0–2 | Did you touch only the files the task needs? Any drive-by refactors or behavior spilling outside scope? |
| Maintainability | 0–2 | Are the names clear? Is the logic more complex than it needs to be? Any duplicated code or hidden state? |
| Boundary coverage | 0–2 | Are the key edges — empty input, missing keys, paths/permissions, time zones, money, ordering — covered by a test or the smoke check? |
| Security | 0–2 | Are path escapes, injection, secret/PII leaks, and external command or network side effects under control? |
| Test honesty | 0–2 | Does the harness still carry its original assertion semantics? Does the smoke check assert output or side effects? Did you avoid skipping or weakening any referee? |

Scoring:
- **8–10**: You may finish; list the remaining risks.
- **6–7**: You may finish, but you must flag the risks and recommendations prominently — don't package it as a risk-free delivery.
- **<6**: Don't finish. Go back to `TRIAGE`/`PLAN_ONE_FIX`, add tests or fixes, or ask the user to confirm scope.

Don't quietly widen scope to fix non-blocking issues the review turns up; list them under "remaining risks / recommendations" in the report. If a risk would affect the correctness of the current task, add a targeted test or a semantic smoke check so it enters the red-green loop.

### Decision table: can I change this?

| What you want to change | Default | Exception |
|------------|----------|------|
| Production code | Fair game | Only within what the task requires |
| Test assertion / snapshot / fixture semantics | Not on your own | Change it only after the user confirms the test is wrong |
| test harness run configuration | Minimal edits allowed | Never lower coverage or strictness |
| Dependencies / lockfiles | Careful; usually blocking | Only when the project states the install method and the change is non-destructive |
| Protected config / real data / deletions and migrations | Stop | Explicit user approval |

## Run Ledger Tooling

This skill ships a lightweight ledger tool and a Markdown template to keep state from drifting during long loops:

- Template: `templates/run-ledger.md`
- Script: `tools/rgm_ledger.py`

**Every red-green run must create a ledger (mandatory — see "create the ledger before you start" in the prep checklist), no matter how small the task.** That's what guarantees you can compute elapsed time at the end:

```bash
python tools/rgm_ledger.py init \
  --task "Fix the markdown link auditor" \
  --workspace "$PWD" \
  --skill-version "red-green-mode v0.19.0" \
  --verify "python -m pytest -q" \
  --out .rgm-ledger.json
```

Append one entry per round:

```bash
python tools/rgm_ledger.py add --out .rgm-ledger.json \
  --state INNER_VERIFY \
  --command "pytest tests/test_parser.py -q" \
  --label TASK_RED \
  --hypothesis "frontmatter parser ignores list fields" \
  --change "link_auditor/parser.py" \
  --result "3 passed" \
  --next OUTER_VERIFY
```

At the end, write the final entry and export the Markdown report:

```bash
python tools/rgm_ledger.py finish --out .rgm-ledger.json \
  --completion-state FULL_GREEN \
  --before-after "TASK_RED 6→0" \
  --semantic-smoke "CLI report JSON asserted" \
  --quality-review "scope ok; remaining risk: YAML subset"
python tools/rgm_ledger.py report --out .rgm-ledger.json
```

> The `## Final` section of `report` computes **`Duration`** for you (derived from `started_at → completed_at`, never persisted, so old ledgers work too). Drop it into the "elapsed time" line of your closing report.

`ledger init` is **mandatory** (see "create the ledger before you start" in the prep checklist) so that you can always compute elapsed time when you finish — **build one no matter how small the task**. **The only exemption**: when you genuinely have no write permission, write the Run Ledger summary by hand in your final answer, note the start and finish timestamps yourself, and compute the total by hand. Beyond that, never skip `init` on the grounds that "the task is small."

## Leave an audit trail every round

The hypothesis-gate line *is* the audit trail. Write one line per round so the user can audit your reasoning afterwards and so someone can take over when you get stuck. **Keep the full output of the last failure plus a one-line repro command** — don't just record "it went red."

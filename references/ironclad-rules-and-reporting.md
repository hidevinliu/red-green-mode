# Red-Green Mode · The 9 Ironclad Rules in Full / Danger Signals / Wrap-Up Report

> **When to read**: SKILL.md keeps only the titles and one-liners of the ironclad rules; this page carries the full text of all nine (cheating-excuse table, stop-condition commands, completion-status definitions), plus the danger-signal self-check, the mandatory items in the wrap-up report, and "stopping ≠ dumping a menu".

> The core ironclad rules remain unchanged. v0.20.0 adds a soft minimality
> review to planning and wrap-up; it is not an ironclad rule or a gate.

## Ironclad Rules (what keeps an unattended loop from crashing)

These aren't formatting requirements. They're guardrails that **keep the autonomous loop from running away**. Each one explains its "why" — judge by that.

### 1. 🚨 Never fake green (the big one)
A stuck agent feels a strong pull to silence the judge instead of fixing the code. **All of the following are banned**, because they drain the green light of meaning:
- Deleting / skipping / commenting out a failing test (`skip` / `xfail` / `.only` / commenting out an assert)
- Weakening an assertion (turning `assert x == 5` into `assert True`, or loosening it until it always holds)
- Papering over static checks with `# type: ignore` / `@ts-ignore` / `eslint-disable`
- Mocking out the logic that's actually failing so it "looks like it passes"

Green **must** come from making the code correct. If you're convinced the test itself is wrong, **stop, explain why, and confirm with the user** — don't edit the test on your own.

> **This rule now has an exit-code verifier (v0.10.0)**: `tools/rgm_anticheat.py scan` scans the diff for newly introduced cheats from the list above; exit 1 means cheating. The GATE state is adjudicated by `rgm_gate.py` — on cheating you get `RGM_GATE=FAIL` and `FULL_GREEN` is forbidden. Legitimate exceptions pass with an audit trail via `# rgm-allow: <reason>` (the reason lands in the ledger for audit). This is no longer just a prompt-level ban — it's a mechanical gate.

> **Violating the letter of the rule = violating its spirit.** No "but I'm following the spirit here" holds up. The usual excuses:

| Cheating excuse | Reality |
|---------|------|
| "This test was obsolete anyway, delete it" | Not your call. Stop, confirm with the user, don't edit tests yourself. |
| "Skip it for now, fix it later" | "Later" never comes. A skip is a forged green — a straight violation. |
| "Add a type: ignore so the types pass for now" | That smears over the red, it doesn't fix it. Fix the type itself. |
| "Mock this bit out and it goes green" | Mocking out failing logic = pretending it runs. That's cheating. |
| "Loosen the assertion a little and it passes" | An assertion loosened until it always holds is no assertion. |

### 2. 🛑 Stop conditions (no infinite loops, no burned budget) — stalling is decided by exit code, not by eye

The primary judge against stalling is **`rgm_ledger.py stall-check`** (its exit code), not "I don't feel like I'm going in circles" — same principle as the ironclad rule that the judge must be external, never the agent itself. Record how many reds remain each round as a number and let the script compute the trend:

```bash
# Record the red count as a number every OUTER_VERIFY round (numbers make trends computable; the free-text result is for humans)
python tools/rgm_ledger.py add --out .rgm-ledger.json --state OUTER_VERIFY --result "4 failed" --red-count 4
# Then let the script decide whether you're stalling (default patience=5: 5 rounds in a row without a new low red count = stalling)
python tools/rgm_ledger.py stall-check --out .rgm-ledger.json   # exit 0=continue, exit 1=stalling, stop
```

- **The exit code is the verdict**: `STALL` (exit 1) → stop right away; report the current state, the last full error, and what you've tried, and **don't keep blind-fixing**. `PROGRESSING` / `GREEN` / `INSUFFICIENT_DATA` (exit 0) → keep going. The criterion is "≥ patience rounds since the red count last hit a new low", which naturally handles the fact that a correct fix can temporarily raise the red count — as long as you're still setting new lows, that counts as progress (see red-light triage).
- **Patience defaults to 5 rounds** (`--patience N` to tune: drop to 2–3 for rush jobs, raise to 8–10 for large refactors). This is the computable version of "stop only after N rounds with no improvement".
- **Wall-clock caps are off by default**: no more 30-minute hard ceiling — a slow task that's still setting new lows shouldn't be killed by the clock, and stall-check already covers stalling. If you really want a wall-clock backstop, the user can set one manually (opt-in), not by default.
- **The other two fast judges still apply** (they fire earlier than stall-check; they supplement it, they don't replace it): (1) the same error survives **3 fix attempts in a row**, or **hypothesis-gate predictions miss repeatedly** → stop; (2) **a check that already went green must not regress** (green→red means you really did introduce a new problem — investigate now, and roll back per ironclad rule 3 if needed).

### 3. 💾 Leave checkpoints, stay revertible (the autonomous loop's safety net)
A loop with no way back only degrades once it drifts. **Checkpoint every time you hit full green** so you can fall back to a clean green state when something breaks:
- Before entering the loop, confirm the working tree is clean and record the starting point (`git stash`, or the base commit of a WIP branch).
- **Every full green = one checkpoint** (`git commit` on a WIP branch / `git stash`); every green state is a safe point to return to.
- If a round's fix makes **an already-green check regress** and you can't find the cause → **revert that round** back to the previous green state and re-hypothesize on a clean base, instead of piling changes onto a dirty one.
- If the starting point is already dirty or broken in several places: don't blind-fix — consider `git bisect` to find which change introduced it.

### 4. ✅ Opening baseline + completion status
- Before you start, run every verifier once and record the **initial red/green counts** (the baseline). Confirm the baseline reds are genuinely red (precondition 2), and tag each red `TASK_RED` / `BASELINE_RED` / `ENV_RED` / `TEST_QUESTION` / `FLAKY`.
- Completion status must be one of these four — never something vague like "basically done":
  - `FULL_GREEN` ✅: every verifier in the outer loop is green, a final full run is still all green, you never touched the verifiers themselves, and (if there's a PRD) every acceptance item has a green verifier covering it.
  - `TASK_GREEN_WITH_BASELINE_RED` ✅/⚠️: all task-related verifiers are green, but a full run still has recorded, unrelated `BASELINE_RED` failures or separately reported `FLAKY` ones. This is a legitimate finish, but you must list the remaining failures and their repro commands.
  - `BLOCKED_BY_INFRA` ⚠️: blocked by `ENV_RED` — dependencies, permissions, environment, runner mismatch — so you can't reliably judge whether the business logic is right.
  - `STOPPED_NO_PROGRESS` ⚠️: `stall-check`'s exit code says you're stalling (patience rounds in a row without a new low red count), or another stop condition fired — same error fixed 3 times, hypotheses missing repeatedly. Include stall-check's verdict line in the report.
- **(With a PRD) done = all green + full PRD coverage**: tests all green but the PRD still has `missing`/`uncovered` items → that is **not** `FULL_GREEN`. Either keep going until it's covered, or list the unfinished items honestly in the "PRD coverage" section and report the status you actually stopped at. Green only means the verifiers caught nothing; it doesn't mean the PRD is done.
- Report `before → after` (e.g. `TASK_RED 2→0, BASELINE_RED 1→1`).

### 5. 🎯 No scope creep
- Change only what's needed to turn this task's reds green. No drive-by refactors of unrelated code, no touching unrelated files.
- Failures that were already red and have nothing to do with this task → tag them `BASELINE_RED`, leave them alone, list them separately in the report. Don't decide for the user whether to fix them.
- Before touching tests, lint/type config, CI config, lockfiles, or hooks, decide whether it's a minimal test-harness fix for an `ENV_RED` or a weakening of the judge. The former is allowed but must leave an audit trail; the latter means stop.

### 6. 🌀 Spot and quarantine flaky tests
The unattended loop's worst enemy isn't a steady red — it's a nondeterministic test that flips red→green→red (one that depends on time, randomness, the network, or concurrency ordering). It keeps the loop from ever finishing, and it tempts you to "just tweak it" — straight into ironclad rule 1.
- **Rule out the environment before you pin the flaky label on anything**: state pollution between tests, execution order, leftover temp files, shared fixtures. A lot of "flaky" is really poor test isolation, i.e. a real bug that deserves no exemption. Re-run it alone, in a clean state, to confirm first.
- **Criterion**: with **no changes to the related code** and the environment/isolation problems above ruled out, the same test still flips red and green → treat it as suspected flaky.
- **Handling**: **quarantine it and list it separately in the wrap-up report** (noted as "suspected flaky, root cause needs human confirmation"). **Don't chase a fix, and never weaken or delete it.**
- Flaky tests **don't count** toward the "done = all green" verdict (otherwise one twitchy test traps you forever), but they **must be reported explicitly** — silently skipping them is as dangerous as faking green.

### 7. 🚪 Human gates still apply
Red-green mode grants **no exemption** from existing safety rules: deleting, bulk-moving, archiving, publishing externally, or changing protected config (`*_lint.py`, settings.json) still needs user confirmation. The loop is autonomous only inside the safe zone of "edit code + run verifiers"; it never does anything destructive outside that zone in the name of "getting to green".
- **On the Claude Code runtime**: if you have installed hooks that guard test files and protected config, they are a hard backstop and will block as usual.
- **On other runtimes (Codex, say)**: those hooks may not exist, and you may even be in a full-access sandbox. There the human gates above rest on this rule alone, so be extra restrained — stop and confirm before deleting, sending anything out, or changing config.

### 8. 🪟 Long-run mode: externalize before you restart, never rely on memory
(Long-run mode only.) The deadliest thing in a long run isn't a red light, it's **context quietly filling up**. The further in you go, the more the agent forgets; decision quality decays with elapsed time until the window blows and the whole run is wasted.
- **The real state lives on disk, not in context**: write the ledger / PROGRESS.md before every step. Keep only decision pointers and the last few hypotheses in the main context; flush all large output to disk.
- **Cooperate with compaction; don't pretend you can dodge it**: inside a conversational runtime an agent can't stop auto-compaction and can't open a new window. What it can do is **write state clean before compaction hits**, then re-bootstrap afterwards. "Losing it doesn't hurt" comes from externalizing, not from "opening a new window yourself" — that isn't in the agent's hands (see §4).
- **After a checkpoint, trust only files**: bootstrap resumes from the ledger + PROGRESS.md; don't replay history. If the files aren't enough to resume from, your handoff was too thin — fill it in, then restart.
- **Never assume you can read the real token %**: use the chars/4 + round-count dual trigger (rgm_window). Better an early checkpoint than a gamble.

### 9. 🔀 Parallelize separable tasks: never edit a shared tree in parallel (parallel mode only)
The most dangerous temptation in parallel mode is "split it across two agents and it goes faster". But red-green mode judges one codebase with one test suite:
- **Multiple writers on one tree = races plus a fake green**: you can't tell who turned what red, and the exit code loses its meaning. If you must parallelize, isolate physically with git worktrees — one agent per worktree, still.
- **No partition gate, no parallelism**: `rgm_partition.py check` exits non-zero (units share files or tests) → fall back to single-agent or long-run mode. Don't force it.
- **Always run the full suite on the merged tree**: units being "green separately" is not "green together". A single writer does the merge, and any red the merge introduces must be fixed to green before you finish.
- **Parallelism is for safely separable speedups, not for "I want it faster"**: forcing an inseparable task into parallel mode is slower and flakier.

## 🚩 Danger signals — stop the moment one appears

Self-check: the instant any thought or action below surfaces, you're in violation. Stop.

- "Skip or delete this test and it goes green" / "Add an ignore or loosen the assertion to get past it" → cheating (rule 1)
- You see red and **edit code immediately**, unable to state a root-cause hypothesis → you skipped the hypothesis gate (violates "diagnose first")
- Staring at a pile of reds and **fixing them one by one** without looking for the shared root cause → no triage (red-light triage)
- Running the **full integration suite after every edit** → you didn't split inner and outer loops (slow and expensive)
- A change turns something red, you want to back out, and there's **no checkpoint to return to** → you left no escape route at the start (rule 3)
- Still fixing the same error on the fourth attempt → you blew past a stop condition (rule 2)
- A test flips red and green and you want to "tweak it until it's stable" → flaky tests get quarantined, not edited (rule 6)
- (Long-run mode) Many rounds in with **no ledger / PROGRESS.md**, keeping everything in context → nothing externalized; the window will blow sooner or later (rule 8)
- Starting the loop **without running `rgm_ledger.py init`** first (skipped because "the task is too small") → violation; you won't be able to compute elapsed time at wrap-up (creating the ledger first is a mandatory setup step; the only exemption is having no write permission)
- The wrap-up report **omits "elapsed time"** (total wall clock + number of rounds) → a missing item, as serious as omitting the completion status; not a valid wrap-up
- Obvious ambiguity but you skipped the Intake Router and forced a doc-only / research / test-less task into red-green mode → routing failure; triage with `rgm_intake.py` first
- No tests, no reproduced bug, yet you start reading code, building theories, and editing while **not a single red-capable command exists** → you skipped the red-signal construction gate (no entering `BASELINE` until you have a red to fix against)
- On a large or non-trivial code task, skipping the Context Pack / CodeMap and going straight to repo-wide grep, random file reads, or edits from memory → context drift; run `rgm_context_pack.py build` or `rgm_codemap.py build/select` first — but never treat a context pack or codemap as proof of completion
- (With a PRD) Declaring "the PRD is done" because the whole suite is green, without checking acceptance items one by one → green ≠ done; you're dropping requirements (PRD completeness gate)
- Wrapping up **without running `rgm_gate.py`**, or reporting `FULL_GREEN` after it returned `RGM_GATE=FAIL` → you bypassed the mechanical enforcement gate (v0.10.0 GATE state)
- Treating the MCP return of `repo.patch.apply` / `repo.verify.run` as proof of completion instead of going on to run the verifier + `rgm_gate.py` → tool output overreaching; write tools must set `not_completion_proof=true`
- Swapping a `verifier` in `acceptance-contract.json` for `echo PASS` / `true` to slip through → attestation catches it, and it's cheating (rule 1)
- Wrapping up or stopping by dumping a multiple-choice scope menu — "(1) do A (2) do B (3) finish" — for the user to pick from → violates the autonomy policy (see "Stopping ≠ dumping a menu"): do the reversible things yourself, stop only at hard gates marked `gate` in the contract, and ask only single factual questions
- Finishing as soon as a milestone contract (`scope: milestone-N/M`) goes green and stopping to ask "which one next?" → you never advanced to `next_milestone`, mistaking a green milestone for a finished project
- Wanting to say "basically done" without full green → a false report that defeats the entire point of this mode

## Wrap-Up Report

When the loop ends — full green, or a stop condition fired — report:
- **Result**: `FULL_GREEN` / `TASK_GREEN_WITH_BASELINE_RED` / `BLOCKED_BY_INFRA` / `STOPPED_NO_PROGRESS`, plus which round you stopped on and why
- **Intake Snapshot**: route / confidence / recommended mode / auxiliary skills / required gates / runtime policy; if you didn't run `rgm_intake.py`, explain why the task was unambiguous
- **Context Pack / CodeMap Snapshot**: paths to `.rgm/context-pack.json/md` and `.rgm/codemap.json/md`, plus a summary of selected_files / dependency_edges / risk_files / unknowns; if you didn't run them, explain why the change was small enough not to need a context pack
- **PRD coverage** (mandatory when there's a PRD): list every acceptance item → ✅done / ⚠️uncovered / ❌not done, with its verifier. Any ❌ or ⚠️ means **don't report FULL_GREEN**. With no PRD, note "no PRD". You can push this into the ledger report with `rgm_ledger.py finish --prd-coverage "..."`.
- **Elapsed time**: total task time (real wall clock from start to wrap-up) + how many rounds you ran; long-run and parallel modes should add timings for key phases. The data comes from the ledger's `started_at → completed_at` — `rgm_ledger.py report` already computes `Duration`; without a ledger, work it out by hand from the start time. **Elapsed time is a cost/efficiency metric, not the judge of completion** (completion is still decided by verifier exit codes). If you stopped at `STOPPED_NO_PROGRESS`, attach stall-check's verdict line explaining which round it was and why it called a stall. No wall-clock cap is set by default (see rule 2); mention whether you hit one only if the user manually enabled a wall-clock backstop.
- **before → after**: how each verifier's red count changed, broken down by tag (`TASK_RED`, `BASELINE_RED`, `ENV_RED`, `FLAKY`)
- **Run ledger summary**: state / command / label / hypothesis / change / result for each round
- **Checkpoint summary**: starting point, touched files, whether a rollback was needed and performed
- **Quality Score**: total across Scope / Maintainability / Boundary / Security / Test honesty, with reasoning
- **Minimality review** (when relevant): kept / simplified / deferred; findings by tag and any ceiling + revisit trigger
- **Change list**: which files you touched and why (with checkpoint/commit references for traceability)
- **What you left alone**: unrelated pre-existing failures, suspected flaky tests (with the last full error + a repro command)
- **Real hard gates (if any)**: see "Stopping ≠ dumping a menu" below — list only the hard gates marked `gate` in the contract, each with its single factual question. No "which should I do next" menus.

Never report "done" when it isn't fully green — that defeats the entire point of this mode.

### 🚫 Stopping ≠ dumping a menu (aligned with the operator autonomy policy)

When wrapping up, or stopping for any reason, **never** hand the user a multiple-choice scope menu — `(1) do A (2) do B (3) do C (4) finish` — to rule on. That's exactly the anti-pattern users hate most. Red-green mode's core belief ("it can only run unattended if there are no ambiguous decision points") extends to the wrap-up:

- **Push the reversible things through on your own** (naming, structure, wording, which implementation to pick, where a file goes, which tool to call, incremental polish) — these never become wrap-up questions. Just do them.
- **Stop only at real hard gates**, and when you do, **ask only the single factual question that gate exists to ask** — never a multiple-choice question. Hard gates = the seven gate kinds marked by the contract's `gate` field (touching another repo's runtime / deleting, bulk moves, overwriting / changing protected config / external sending or export / opening a privacy-sensitive archive / the agent's dissent ledger / confirming the target once before an autonomous run). Use `acceptance_contract.py ready` to split hard gates out from the autonomous items (`gates[]`); the loop may only stop on `gates`.
- **A green milestone ≠ a finished project**: if the contract's `scope` is `milestone-N/M`, going green means **automatically advancing to `next_milestone`** (or honestly reporting "X remains in the project; the next milestone is Y"). **Don't** stop to ask "which do you want next?". Only `scope: project` at full green is a real finish.
- A "recommendation" has to make the call for the user. Presenting "stop at this milestone" as a recommendation and pairing it with a multiple-choice list is a double violation: you didn't decide for them, and you tossed the decision back as if that were politeness.

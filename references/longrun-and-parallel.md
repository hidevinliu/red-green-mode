# Red-Green Mode · Long Run & Partitionable Parallelism (operational details)

> These two are **optional outer modes**, off by default (pass no flag = single agent, behavior unchanged). SKILL.md keeps only the summary plus ironclad rules 8/9 (the hard rules that never move); **when you actually enter long run / parallel mode, read this page for the full how-to**.
> The immovable hard rules still live in SKILL.md, "Ironclad Rules" §8 (externalize state before restarting) and §9 (never write to a shared tree in parallel).

---

## Long Run Mode (going long without blowing the context window)

Turn on long run mode when a task will take many rounds, or when it will fill the context window. The core idea: **the main agent tracks only "how far it got"; everything else is pushed to disk and to one-shot subagents** — the context stays disposable and rebuildable, so a long run never blows the window. This wraps around the production state machine; it does not change the state machine itself.

> ⚠️ First, keep the **three different "checkpoint/budget" concepts** apart (the easiest thing to get wrong when combining modes):
> - **Progress budget** (anti-stalling, ironclad rule 2): same error fixed 3 times / no progress → stop.
> - **Rollback checkpoint** (anti-drift, ironclad rule 3): `git commit` at every full green, so a bad turn can be backed out.
> - **Context checkpoint** (anti-window-overflow, added by this mode): as you approach the window limit, **write the handoff**. The actual restart/compaction is done by the runtime, a human, or an external looper — **not by the agent itself** (see §4).
>
> The three are orthogonal. Don't blur them into one word.

### 1. Startup: externalize the state, tag the mode
In a long run the real state lives on disk, not in the agent's head. Create the external state up front (reusing the Run Ledger):

```bash
python tools/rgm_ledger.py init --task "<goal>" --workspace "$PWD" \
  --mode longrun --window <1000000 CC / 200000 Codex> --out .rgm-ledger.json
```

Then create the human-readable handoff `PROGRESS.md` (template: `templates/PROGRESS.md`), plus `worker-reports/` and `logs/`. **Write the ledger and PROGRESS.md before taking the next step** (write-ahead); after any restart, trust only the files.

### 2. Hand heavy work to one-shot workers; take back conclusions only
Token-heavy actions — the full `BASELINE` run, reading large files, searching, the whole `OUTER_VERIFY` pipeline — go to a **one-shot subagent** that returns at most 1500 tokens of conclusion (red-signal label + failure summary + next step); the full output lands in `worker-reports/` or `logs/`. **Only the orchestrator edits code** (single writer; workers read and run, never write). Any single tool result over 2000 tokens goes to disk and comes back as a summary — never pipe it straight into the main context.

### 3. Window sentry: two triggers on estimated usage
A runtime can't read its own true context usage percentage from inside the process, so estimate it with a proxy metric (`tools/rgm_window.py`):

```bash
python tools/rgm_window.py zone --runtime claude-code --round <N> --ingested <chars/4>
# -> {"zone":"green|amber|red","checkpoint":bool,"reason":...}
```

- **green**: keep going.
- **amber** (soft = 55% of window, or round 18): wrap up the current subtask, don't start anything big.
- **red** (hard = 70% of window, or round 25): write the handoff immediately and move to the §4 checkpoint procedure.

Estimate ingested tokens with `rgm_window estimate --chars N` (chars//4), accumulate them, and write the total into the ledger budget:

```bash
python tools/rgm_ledger.py budget --out .rgm-ledger.json --round <N> --ingested <T> --zone <zone>
```

> Thresholds are tunable: if you still blow the window occasionally in practice, lower `--hard-round` (e.g. 25 → 18), or wire in the runtime's exact token API.

### 4. Context checkpoint: the agent writes the handoff, but opening a new window isn't its job

> ⚠️ **Runtime reality**: in a conversational runtime like Claude Code, **an agent cannot restart itself into a clean new session from inside that session** — the primitive doesn't exist. The one thing an agent can do autonomously is **write its state down cleanly before the window fills, so that whoever restarts it, whenever, can pick up where it left off**. The actual context reset is triggered by one of the three below (the agent controls none of them, least of all the third):

| Form of reset | Triggered by | Clean new context? |
|---|---|---|
| runtime **auto-compaction** | the runtime, automatically at its threshold | lossy in-place compaction in the same session (not a new window, but good enough) |
| **manual** new session | you | ✅ genuinely clean, but needs a human |
| **external looper** (headless `claude -p` wrapped in a while loop) | an external script, `tools/run_longrun.sh` (branch isolation / hard budget) | ✅ a new process every round; this is what real unattended running relies on |

**So when you hit red, the agent prepares for a restart — it does not restart itself:**

1. **Write the handoff**: `python tools/rgm_ledger.py checkpoint --out .rgm-ledger.json --at-round <N> --reason "<trigger>" --handoff PROGRESS.md`, plus an update to `PROGRESS.md` (how far you got / key decisions / next step / traps).
2. **Self-check**: "If I got killed right now, or wiped by compaction, could someone resume from the ledger + PROGRESS.md alone?" If not, write more down.
3. **Hand off to the reset** (the agent doesn't open a window itself):
   - **Default in a conversational runtime (the only self-contained defense)**: keep working and let auto-compaction squeeze the context; **the first action after compaction is to re-read** `rgm_ledger bootstrap` + PROGRESS.md and restore the details that got squeezed out.
   - **A genuinely new window**: a human opens a new session, or the external looper `tools/run_longrun.sh` triggers one (branch isolation + hard budget + human review before merging once it's green; same story for Codex — an external `/goal` loop, not the agent starting itself).
4. **First action after any reset/compaction**: `python tools/rgm_ledger.py bootstrap --out .rgm-ledger.json` + read PROGRESS.md → resume from the cursor, **don't replay history**. If it really is a new session (manual or external), run `python tools/rgm_ledger.py resume` once you've picked up (marks `resumed=true`).

> In one line: **the agent's job is to make its own context cheap to lose (state externalized), not to open a new window (that's the runtime's, the human's, or the external looper's job).**

### 5. Reconciling the progress budget (important)
A fixed "10-round cap" would stop a long run from ever running long, so **drop the hard round cap and rely on softer judges**: (1) a gate that already went green must never regress (green → red gets investigated or rolled back immediately); (2) a **per-round** `stall-check` (red count fails to hit a new low for `patience` rounds → stalling, and the exit code calls it; see ironclad rule 2) — this is the primary judge against spinning in place during a long run; (3) a **cross-checkpoint backstop**: if the ledger cursor doesn't advance across 3 consecutive context checkpoints → `STOPPED_NO_PROGRESS`, stop and ask a human. You get to run long and still catch stalling.

## Partitionable Parallelism (worktree isolation)

Only go parallel when the task splits into **mutually independent** units (each with its own test target, sharing no files that will be rewritten). **Safety first, not speed first** — parallel edits to the same code leave the judge's exit code unable to say who caused what (a form of false green), so parallelism is allowed only with physical `git worktree` isolation.

### Partition gate (`DECOMPOSE_GATE`) — fail it and parallelism is off
Verify independence with an objective judge before going parallel; don't go by feel:

```bash
python tools/rgm_partition.py check --units units.json   # exit code 0 or no parallelism
```

`units.json` declares each unit's `paths` and `test` target; the tool checks every pair for overlapping paths/tests (including directory-prefix containment). **A non-zero exit means no parallelism → fall back to single agent / long run mode.**

### Flow

```text
DECOMPOSE_GATE (does rgm_partition exit 0?)
   │no  → fall back to single agent / long run
   │yes
   v
one git worktree per unit → each runs a full red-green loop (single agent, long run can stack on top)
each parallel worker reports its finish state + before→after
   │all green inside their own isolation
   v
MERGE_GATE: a single writer merges → run one full red-green loop over the merged tree
          (merging itself introduces red, so verify everything) → FULL_GREEN to finish
```

- **Never write to the same shared tree in parallel** (see ironclad rule 9): the only way to parallelize is physical worktree isolation, and inside each worktree it's still a single agent.
- **Merging is single-writer**: "each unit green on its own" is not "the whole thing green". After merging, verify the entire tree, and the single writer fixes whatever red the merge introduced.
- The number of units is decided by **how partitionable the work is**, not by wanting it faster. Forcing parallelism on non-partitionable work is slower and flakier — explicitly don't.

---

> Everything below is copied verbatim from SKILL.md (the v0.19.0 slim-down; see the references index table in SKILL.md). It only changed location — not a word of the text changed.

## Three modes: single agent by default, long run / parallel optional

Red-green mode is a **single-agent** loop by default (which is what almost all of this document describes). The two **optional** outer modes are turned on as needed; default behavior doesn't change:

| Mode | When to turn it on | What it solves | Details |
|------|--------|------|------|
| **Single agent** (default) | ordinary tasks | — | this whole document |
| **Long run** | the task will take many rounds / will fill the context | blowing the window on a long run; resuming from a checkpoint | "Long Run Mode" above |
| **Partitionable parallelism** | the task splits into unrelated modules | speed on partitionable work (worktree isolation) | "Partitionable Parallelism" above |

Pass no flag and you get the single agent, behaving exactly as if no mode existed. The two outer modes stack (each worktree in a parallel run can itself use long run mode). **Never force any mode on without an objective verifier**, and **never force parallelism when the partition gate fails** — the prerequisite gate (next section) applies to every mode.

## Long Run & Partitionable Parallelism (optional outer modes · operational details moved out)

Two **optional** outer modes, off by default (pass no flag = single agent, behavior unchanged). This section keeps only "when to turn it on + the hard rules you must obey"; **the full how-to (startup state externalization / worker offloading / two-trigger window sentry / checkpoint resume / partition gate / merge gate) is in `references/longrun-and-parallel.md`** — read it when you actually enter long run / parallel mode.

- **Long run** (the task takes many rounds / will fill the context): the real state is externalized to `.rgm-ledger.json` + `PROGRESS.md` (`rgm_ledger.py init --mode longrun`); heavy work goes to one-shot workers that return at most 1500 tokens; `rgm_window.py` fires on two triggers (soft = 55% of window or round 18, hard = 70% or round 25) and you write the handoff as you approach them. **Ironclad rule 8**: externalize before restarting — the agent's job is to make its context cheap to lose (state written down cleanly), **not to open a new window** (that belongs to runtime auto-compaction, a human, or the external looper `run_longrun.sh`); after compaction/restart the first action is `rgm_ledger bootstrap`. The progress budget rides on stall-check plus the cross-checkpoint backstop, not on a fixed round count.
- **Partitionable parallelism** (the task splits into mutually independent units): **pass the partition gate first**, `rgm_partition.py check --units units.json` (exit 0 or no parallelism), then give each unit its own git worktree running a single agent. **Ironclad rule 9**: never write to a shared tree in parallel; merging is single-writer, and after the merge you **verify the entire tree**, with `FULL_GREEN` required to finish (each unit "green on its own" ≠ "green as a whole").
- **Don't mix up the three checkpoints/budgets**: progress budget (anti-stalling, ironclad rule 2) / rollback checkpoint (anti-drift, ironclad rule 3) / context checkpoint (anti-window-overflow, added by long run) — orthogonal, so don't blur them into one word.

# Red-Signal Construction (PRECHECK prerequisite 1, expanded)

> **When to read this**: when `PRECHECK` finds that **prerequisite 1 (an objective verifier exists) is not met** — there are no tests, or the bug isn't yet reproduced by any command.
>
> **In one line**: the entire red-green machine rests on having **one red signal you can fix against**. So when that red signal doesn't exist, **building it is the highest-leverage move available right now**, and deserves disproportionate effort. Adapted from Matt Pocock's `diagnosing-bugs` Phase 1 ("Build a feedback loop" — *This is the skill*).

---

## Why this step is worth a big bet

Red-green mode's belief is that "the exit code decides right from wrong". But before an exit code can decide anything, there has to be a command that **goes red on the current problem**. Beginners skip this step — they stare at the code and *think* about the root cause, or wave at it with "let's write a test first" — and then start changing things with no feedback at all.

> Build the right feedback loop and the problem is 90% solved. Bisection, hypothesis testing, and instrumentation only **consume** that loop. Without it, no amount of staring at code will help.

So on this step: **be aggressive, be creative, refuse to give up.**

---

## Done criteria (when this gate counts as passed)

You can name **one command that has actually been run at least once** (paste the invocation and its output), and it satisfies all of:

- [ ] **red-capable** — it really walks the broken code path and asserts **the exact symptom the user described**, so it goes red on this bug and green once the bug is fixed. Not "it runs without errors" — "it catches this specific bug".
- [ ] **deterministic** — same verdict every time. For a nondeterministic bug, first push the **reproduction rate** up to something debuggable (50% is debuggable, 1% is not).
- [ ] **fast** — seconds, not minutes. **"A 30-second flaky loop is barely better than none; a 2-second deterministic loop is what tight means."** That's a debugging superpower.
- [ ] **agent-runnable** — you can run it unattended; only pull a human into the loop, through an HITL script, when a click is genuinely unavoidable (see below).

**Hard discipline line**: if you catch yourself reading code and building theories before this command exists — **stop**. Jumping straight to hypotheses is exactly the failure this gate exists to prevent. **No red-capable command, no entry into `BASELINE`.**

---

## Ten rungs for when you can't get a red signal (try roughly in this order)

1. **A failing test** — at whatever seam reaches the bug (unit / integration / e2e).
2. **A curl / HTTP script** — against a running dev server.
3. **A CLI invocation with fixture input** — diff stdout against a known-good snapshot.
4. **A headless browser script** (Playwright / Puppeteer) — drive the UI, assert on DOM / console / network.
5. **Replay a real trace** — save a real request / payload / event log, then replay it through the code path in isolation.
6. **A one-shot harness** — stand up a minimal subsystem (one service, dependencies mocked) and trigger the broken code path with a single function call.
7. **A property / fuzz loop** — when the bug is "sometimes the output is wrong", run 1000 random inputs to find the failure pattern.
8. **A bisection harness** — when the bug appeared between two known states (commit / dataset / version), automate "switch to state X → check → repeat" so `git bisect run` can drive it.
9. **A differential loop** — run the same input through the old version vs the new one (or two configurations) and diff the output.
10. **An HITL bash script** — last resort. When a human really has to click, drive the **human** with `scripts/hitl-loop.template.sh` (the template shipped in Matt's original repo) so the loop stays structured and the captured output feeds back to you.

> Build **any one** of these loops and the bug is 90% fixed.

---

## Tighten the loop — polish it like a product

Once you have *a* loop, don't stop — **tighten** it:

- **Faster?** Cache the setup, skip unrelated initialization, narrow the test scope.
- **Sharper signal?** Assert on the **specific symptom**, not on "it didn't crash".
- **More deterministic?** Pin the clock, seed the RNG, isolate the filesystem, freeze the network.

---

## Worked example: a repo of small standalone scripts with no test suite yet

A common hard case: the codebase is a pile of small standalone scripts (linters, hooks, one-off CLI tools) with no test suite to hang a red signal on. Three of the ten rungs above carry most of the weight here:

- **CLI + fixture (rung 3)** — the default for scripts: build a minimal input directory / sample file / fake config, run the script, assert on the exit code plus the output that matters (stdout JSON fields, files written, records archived). Far stronger than running `--help`, which is a **false green**.
- **One-shot harness (rung 6)** — for logic bugs: `import` the function directly, feed it an input that triggers the symptom, assert on the return value or side effect. Seconds, deterministic.
- **Differential (rung 9)** — for "did the behavior change after I edited the script?": run the same real inputs through the before and after versions, diff the output, and pin down that whatever shouldn't have changed didn't.

The bar (matching red-green's "a smoke test must assert something"): assert at least **one observable thing** — exit code plus an output field, a filesystem side effect, or written content. Proving that "the script runs" or "the HTML compiles" doesn't count.

---

## When you truly can't build one (honest limits)

If you've tried all ten and still can't produce a red signal, **stop and say so explicitly** instead of barreling into the hypothesis phase. List what you tried, and ask the user for: (a) access to an environment where it reproduces; (b) a captured artifact (HAR / log dump / core dump / timestamped screen recording); (c) permission to add temporary instrumentation in production.

---

## Once you have the signal → back to red-green

With that red-capable command in hand:

- It **is** TDD's red test — go straight into red-green's `BASELINE`.
- It **is** the bug's reproduction — continue with `diagnosing-bugs`: minimise → hypothesis → instrument → fix → regression test; red-green's exit code decides when it's green.

This gate's only job is turning "no judge" into "one red signal you can fix against". Once you have it, return to `PRECHECK` and enter the loop normally; the final verdict on completion still comes from verifier exit codes / `rgm_gate.py` / PRD coverage.

---

> Everything below is copied verbatim from SKILL.md (the v0.19.0 slim-down; see the references index table in SKILL.md). It only changed location — not a word of the text changed.

## Red-Signal Construction Gate

When prerequisite 1 (an objective verifier exists) isn't met — no tests, or the bug isn't reproduced by any command — **don't just toss out "switch to TDD / switch to debugging" and call it done**. The entire red-green machine rests on having one red signal you can fix against, so **building that red signal is itself the highest-leverage move available right now** and deserves disproportionate effort (adapted from the core insight of Matt Pocock's `diagnosing-bugs` Phase 1: **build the right feedback loop and the problem is 90% solved**; bisection / hypothesis testing / instrumentation only consume that loop).

**Done criteria** — you can name **one command that has actually been run at least once** (paste the invocation and its output), and it hits all four:

- **red-capable**: really walks the broken code path and asserts **the exact symptom the user described** — goes red on this bug, green once it's fixed. Not "it runs without errors", but "it catches this specific bug".
- **deterministic**: same verdict every time (if it's flaky, first push the reproduction rate up to something debuggable: 50% is debuggable, 1% is not).
- **fast**: seconds, not minutes. "A 30-second flaky loop is barely better than none; a 2-second deterministic loop is what tight means."
- **agent-runnable**: runnable unattended; if a human click is unavoidable, use `scripts/hitl-loop.template.sh` to pull the human into the loop too.

**Hard discipline line**: reading code and building theories before the command exists → **stop**; that's exactly the failure this gate guards against. **No red-capable command, no entry into `BASELINE`.**

The ten rungs for when you can't build one (failing test → curl → CLI+fixture → headless browser → replay a trace → one-shot harness → property/fuzz → bisection → differential → HITL bash), how to tighten the loop (faster / sharper signal / more deterministic), and how this lands for a repo of small standalone scripts are all in `references/red-signal-construction.md`. Once you have the signal: it's TDD's red and it's the bug's reproduction — return to `PRECHECK` and enter the loop normally.

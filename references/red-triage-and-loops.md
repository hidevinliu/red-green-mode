# Red-Green · Three-Layer Loop / Check Pipeline / Red-Light Labels and Triage / Hypothesis Gate

> **When to read**: When a batch of red lights lands and you don't know which one to attack first, when you need to label a red light (including the 8 `ENV_RED` subtypes), when you want to know how the inner and outer loops split, or when you need to know what to fill in before you touch anything each round.

> Everything below was moved verbatim out of SKILL.md (the v0.19.0 slim-down; see the references index table in SKILL.md). Only its location changed, not a word of the text.

## Three-layer loop: inner → outer → finish (don't re-run everything every round)

The beginner loop is "edit, then run everything" — slow and expensive, ten minutes per round once integration and end-to-end tests are involved. Engineers split it in two, which fits the principle that verification costs more than generation and still has to earn its keep:

```
Inner loop (fast, for iterating):   run ONLY the one failure you're fixing right now (e.g. pytest path::test_x)
                                    → feedback in seconds; test hypotheses fast until that one goes green
Outer loop (full, for confirming):  only once that one is green, run the whole pipeline from stage 1
                                    → catches the collateral regression where fixing this broke something else
Finish (after all green):           run everything clean one more time; declare done only once it holds green
```

> The inner loop buys speed, the outer loop buys correctness. **Worried about introducing new problems? That's what the outer loop is for** — not running everything after every character you type.

**Smoke tests must assert something**: don't declare it working just because `--help` ran or the command exited 0. For a CLI, API, or script, assert at least one key output, file side effect, returned JSON field, or database state. Otherwise you get the fake green where "all tests pass and the command exits 0, but the output means the wrong thing."

**An all-green baseline still has to match the user's goal**: if the baseline is already green, don't edit code in the name of "optimization." Ask first: did the user state a concrete behavior or invariant that the existing checks don't cover? If not, report `FULL_GREEN`/no-op. If so, write the uncovered invariant as a targeted test or a semantic smoke check, watch it go red, and then enter the loop.

## Check pipeline (fast → slow)

Order the outer loop from cheapest to slowest so problems surface as early and as cheaply as possible:

```
1. Format / lint       seconds, run first
2. Type check          seconds
3. Compile / build     seconds to minutes
4. Unit tests          minutes
5. Integration / E2E   slow, run last
6. Smoke test          actually run one minimal case and assert observable output or side effects; exit=0 alone doesn't count
```

Run only the stages the project **actually has**. Common commands per language are below — detect which set the project uses instead of hardcoding them:

| Stage | Python | JS/TS | Go | Rust |
|------|--------|-------|----|----|
| Lint/format | `ruff check` / `black --check` | `eslint` / `prettier --check` | `gofmt -l` / `go vet` | `cargo fmt --check` / `cargo clippy` |
| Types | `mypy` / `pyright` | `tsc --noEmit` | (the compiler checks types) | (the compiler checks types) |
| Compile/build | (usually none) | `tsc` / `npm run build` | `go build ./...` | `cargo build` |
| Unit tests | `pytest` | `npm test` / `vitest` | `go test ./...` | `cargo test` |

> If the workload is standalone Python scripts (`*_lint.py`, hook scripts, and so on) rather than an importable package: by default, detect `pytest` + `ruff` and actually run the script once as a smoke test.

### Red-light labeling protocol: triage first, fix second

Label every red light before you touch it; the label decides whether you may fix it autonomously. Don't treat every failure as a business bug.

| Label | Meaning | Action |
|------|------|------|
| `TASK_RED` | A stable failure directly tied to the current task | Take it into the inner loop and fix it |
| `BASELINE_RED` | Already failing before you started, and unrelated to the task | Leave it alone; keep it in the report |
| `ENV_RED` | Environment or test-framework problems: missing dependencies, wrong working directory, import paths, runner/version mismatch, permissions, syntax or escaping errors in test files | Minimal test-harness fixes allowed; if the dependency or permission is unobtainable, `BLOCKED_BY_INFRA`; never change business logic to paper over it |
| `TEST_QUESTION` | The test expectation looks wrong, the requirement is unclear, or several tests/doc examples contradict each other | Stop, lay out the evidence, and ask the user to confirm; don't edit the test yourself |
| `FLAKY` | Still flips between red and green after the environment is ruled out | Quarantine and report it; don't chase it with random edits |

### `ENV_RED` subtypes

Every `ENV_RED` needs a second label, because different environment failures come with different levels of autonomy.

| Subtype | Example | Default action |
|--------|------|----------|
| `ENV_IMPORT` | `ModuleNotFoundError` from src layout / PYTHONPATH | Minimal harness fix, e.g. `pythonpath=["src"]` |
| `ENV_WORKDIR` | Command ran in the wrong directory | Re-run from the project root and record the cwd |
| `ENV_RUNTIME` | pytest runs on 3.14, the production smoke test on 3.9 | Use the runtime the project declares; otherwise report a runtime mismatch |
| `ENV_TEST_SYNTAX` | String escaping or syntax error in a test file | Fixing obvious syntax is fine, but never change assertion semantics |
| `ENV_DEPENDENCY` | A third-party package is missing and undeclared | No declared install method → `BLOCKED_BY_INFRA` |
| `ENV_PERMISSION` | Insufficient file or directory permissions | `BLOCKED_BY_INFRA`; don't guess-fix with chmod/chown |
| `ENV_NETWORK` | External network, API, or service unreachable | Quarantine or block; don't mock away the real failing logic |
| `ENV_SECRET` | Missing token/key/credential | `BLOCKED_BY_INFRA`; never fabricate a secret |

**Fixing the test harness is allowed; weakening the referee is not**: you may add missing minimal run configuration (a `pythonpath` in `pytest.ini`, or selecting the runner the project declares) and fix obvious syntax or escaping errors in test files, but you must preserve the original assertion semantics and explain "why this counts as an environment fix." When a dependency is missing, install it only if the project clearly declares the dependency and its install method; otherwise label it `BLOCKED_BY_INFRA` and don't mock or fake an external package to force a green. Any config change that narrows test scope, skips tests, loosens assertions, adds an ignore, or lowers lint/type strictness counts as cheating.

### Red-light triage: which red to attack first

Don't grind through them one at a time. When a batch goes red, **triage** first:

1. **Cluster by shared root cause**: 30 failing tests are usually one `ModuleNotFoundError`, one bad import, or one broken shared function. Fix the single root cause and all 30 go green together.
2. **Always fix the most upstream stage first**: follow the dependency chain — **compile/types > unit > integration**. Once upstream is green, a whole swath of downstream may go green on its own. Chasing downstream symptoms is a disaster.
3. **Expect the red count to rise first**: after you fix a compile error, tests that never got the chance to run now run and produce a fresh batch of reds. That's progress, not regression — this is the "watch the trend, not a single round" clause in the stop conditions.

## Hypothesis gate before every fix (diagnose, then act)

**Never edit on instinct just because something is red** (shotgun debugging). Before you touch Edit each round, fill in the lines below; miss one and you don't get to start:

```
[Round N] Symptom:      <the full error / failing assertion, verbatim — not "it went red">
          → Hypothesis: <I think the root cause is X, because ...>
          → Prediction: <changing A will turn B green without affecting C>
          → Change:     <what I changed, in which file>
          → Actual:     <was the prediction right? yes/no>
```

"Actual: was the prediction right?" is the soul of this gate: **predictions that keep missing mean your model is wrong**, and that cuts your losses earlier than "three failed fixes" does (see ironclad rule 2). This line is also the most valuable thing you can hand a human when you're stuck. Leave real root-cause investigation to `systematic-debugging`; this gate only enforces "no edit without a hypothesis."

**Contradictory test semantics escalate to `TEST_QUESTION`**: if two tests or examples give mutually exclusive semantics for the same edge case, or one fix necessarily turns another test at the same level red, stop guessing at product intent. Stop, list the conflicting examples and the hypotheses you tried, and let the user or the product rules decide.

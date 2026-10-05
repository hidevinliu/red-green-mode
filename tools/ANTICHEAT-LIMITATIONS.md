# Mechanical Enforcement Layer · What It Cannot Catch (Honest Limits)

> Based on an independent adversarial code review on 2026-06-23 plus three rounds of real-world calibration. The mechanical enforcement layer catches the **most common, most mechanical forms of faking green and rule-breaking**, backstopping prompt self-discipline with exit codes. But it is a **heuristic** — not taint analysis, not a type system, not formal verification. Everything below it **cannot catch**; those need human review. Never read "the gate went green" as "definitely nothing wrong."

## What rgm_anticheat (anti-cheat) cannot catch

1. **Semantic weakening outside the assertion line**: since v0.21.0 any edit to an assertion line itself (`assert total == 42` → `assert total is not None`, a loosened threshold) shows up as a *rewritten* assertion and FAILs under the default `agent` profile (it is only a WARN under `--profile review`). What is still **not caught** is weakening that leaves every assertion line untouched: changing fixture data, a helper the test calls, or a mock's return value so the bug no longer shows. (Review the load-bearing assertions yourself, or run them for real with `--run-contract-verifiers`.)
2. **Aliases and dynamic forms**: since v0.21.0 marker aliases (`later = pytest.mark.skip`) and module-level `pytestmark` are caught, because any reference to the skip/xfail markers counts. Still slipping through: `from pytest import skip; skip()`, skips constructed at runtime, and indirect mocks. The regexes match the common spellings; anything written around them slips through.
3. **Strings spanning lines**: `_in_string` is a **single-line** char walk. It does not track triple-quoted multi-line strings, so a pattern inside one can be judged the wrong way (rare).
4. **`patch(` noise**: an HTTP `patch(url)` or a custom `patch` helper raises a category 7 **warn** (it does not fail the gate, it is just noise).
5. **Narrowing with `--paths`**: use scope filtering only to narrow down to the full set of files this run touched. Do not use it to exclude the file you cheated in — that is shrinking the referee's field of view to nothing.

## What rgm_constraints (the constraint gate) cannot catch

6. **It covers only the mechanically checkable subset**: read-only paths, wikilinks in outbound documents, protected config (`settings.json`, `*_lint.py`). **Wording, tone, jargon, business policy, `[inference]` tagging** and rules like them go into the human-read checklist only and **never auto-FAIL**. The vast majority of rules in a CLAUDE.md cannot be scripted — this gate does not pretend to enforce the whole book.
7. **Path extraction is heuristic**: on a read-only line, backtick-quoted tokens win; otherwise it grabs whatever is path-shaped (contains `/`, is a dotfile, or matches `NN-name`). Phrasing that loose (`"don't touch that directory"`, no concrete path) yields nothing → it goes to the human checklist marked "verify by hand," rather than being silently swallowed.

## The trust boundary of rgm_gate (the completion gate)

8. **Contract `status` is trusted by default**: without `--run-contract-verifiers`, the gate trusts the `status` maintained by the red-green loop (the loop updates it by actually running the tests). To make the gate re-run every green item's verifier itself, **pass `--run-contract-verifiers` explicitly** (recommended in the GATE state).
9. **No verifier means no green**: an empty `verification_commands` gives VERIFIER=FAIL. Red-green mode assumes an objective referee; with no referee it never PASSes.

## What the attestation lock covers

10. It locks a SHA-256 over each item's `(id, check_type, expect, verifier)` set. Change any of those four without re-running `attest` and `verify-attest` exits 1. What it does not lock: **adding or deleting a whole item** (the change in count changes the hash, so that does get caught; but "add a padded green item and re-attest at the same time" is deliberate human whitewashing — only review catches it).

## Additions from the second four-way review (2026-06-23)

11. **Editing production code to satisfy the test leaves anti-cheat essentially defenseless.** Anti-cheat only looks at test-side moves in the diff (skip / deleted assertion / weakening). An agent that rewrites production code to `return 42` so `assert == 42` passes, or hardcodes a specific input — **the test passes legitimately and anti-cheat cannot see it** (it only warns on an explicit comment like `# hardcode/to pass`). **The only defense is a semantic smoke assertion against real observable output or side effects** (SKILL: "smoke tests must assert"), plus `--run-contract-verifiers` running the real tests. A green gate is not correct code. **v0.22.0:** `rgm_pointpatch.py` now covers the narrowest form of this, a fix that changes behaviour only at the tested inputs (`return 42` for exactly the asserted case, lookup tables of expected outputs). It does not cover a fix that changes a whole region the wrong way; see `bench/pointpatch/README.md` for its measured catch and false-alarm rates.
12. **The 39 eval scenarios are a subjective rubric, have no runner, and have never been executed.** `evals/evals.json` is a guardrail checklist for a human or an LLM to score against `expected_output` — **it is not automated verification**. When you report, **do not list it next to the unit tests and say "both pass"**: the unit tests (`pytest tests/`) actually execute; the evals do not.
13. **The referee's commands come from the ledger written by the party being judged** (`verification_commands` run with `shell=True`). Attestation only blocks "swap it out after it is locked"; it does nothing about "write a weak verifier (`true` / `echo PASS`) from the start." **The ledger is trusted input** — whoever can write the ledger can make the gate run any command and rule it PASS. "Contracts carry real test verifiers, and GATE runs them by default" adds one layer of independence, but at root a human still has to make sure the verifier is a real target.
14. **Operations are not unattended-grade** (the review scored it 3/10), though three guardrails have been added since (v0.10.1, 19 unit tests): (1) `secret-guard.py`, a PreToolUse hook that denies any command line containing a secret (keeps tokens out of shell history — one really did leak during this work); (2) `skill-live-guard.py`, which detects the live skill being swapped out from under you by a `git checkout` (this really did happen); (3) `skill-dev-worktree.sh`, which moves development into an isolated worktree instead of touching the live copy. **Still not done**: pointing the live copy at a tag instead of a checkout-able branch (a larger structural change); and (2) and (3) are conventions/helpers — someone has to remember to use them, or wire them into SessionStart. Fully safe unattended operation is still an asymptote.

15. **v0.18.1 three-lens audit remediation (fixed / honestly not fixed).** An external three-lens review (process / code / QA) found real defects; these landed in v0.18.1:
    - **Fixed (adjudication layer)**: A1 — the stop hook moved from "grep for `RGM_GATE=FAIL`," a blacklist, to **fail-closed**: when the gate cannot run or cannot adjudicate (corrupt ledger → exit 2, empty stdout) it **blocks** the stop instead of silently letting it through. A2 — the gate's CONTRACT check moved from the blacklist `{missing, uncovered, red}` to a **whitelist**, "anything not `green` is FAIL," closing the hole around `todo` and any status outside the enum. D1 — atomic ledger writes (tmp + rename); D2 — `timeout` on verifiers; D3 — a corrupt load exits 2 (no longer colliding with the stall check's exit 1).
    - **Fixed (anti-cheat regex blind spots)**: B1 — category 4 now uses `IGNORECASE` (an uppercase `# NOQA` no longer slips past). B2 — category 5 now covers the parenthesized `assert(True)` plus `assert <non-zero constant>` (anchored at end of line or a comma, so a real comparison like `assert 200 == x` is not a false positive), which also cleared the pre-existing false positives on `assert 1 == x`.
    - **Not fixed (stated honestly) — A4**: you can bypass the SHA lock by running `attest` first, then deleting the whole `attestation` field and swapping in a weak verifier. **Why we are not fixing it**: attestation is **optional** hardening, and "no attestation = FAIL" would punish every legitimate contract that chose not to attest. A4's real defense belongs to the trust boundary in §13 — anyone who can write the contract file could already write the ledger; attestation only blocks "swap it out after it is locked," not "never lock it / delete the lock on purpose." Actually closing A4 means making attestation state external and sticky (once you have attested, you may not quietly revert to unlocked), which is a much larger stateful change.
    - **A heuristic is still a heuristic**: C1 blocks "just mark it green / skip the tests" at the intake door by keyword match. Reworded phrasing can still get through — it reduces, rather than eliminates, cheating intent buying a ticket in, and the gate/anti-cheat still have to backstop it.

16. **v0.21.0 measured benchmark (bench/).** The scanner is now scored, not just described: 55 seeded cheats
    (`bench/cheats.py`) for recall and 630 merged human PRs from 21 popular repos for noise. Before v0.21.0 it
    blocked 32/55 cheats and 10.5% of honest PRs; after, `--profile agent` blocks 55/55 and `--profile review`
    misses none (41 blocked, 14 warned) while blocking 3.3% of honest PRs. Numbers, method and a line-by-line
    audit of every remaining block: `bench/README.md`. What the numbers do **not** say:
    - **The cheat corpus is hand-written.** 55 techniques we know about; recall on techniques nobody has written
      down yet is unknown. One seeded cheat (a bare `return` before the assertion) needs program semantics and is
      listed as out of scope rather than silently dropped.
    - **`--profile review` is for human PRs only.** It turns a rewritten assertion into a WARN, which means the
      classic "edit the expected value to match the bug" passes the exit code. Never run an agent loop with it.
    - **Moves are exempt.** An assertion removed and re-added verbatim elsewhere is not a finding; if it was moved
      into a dead block, only the skip / `if False:` / `except AssertionError` rules catch that.
    - **Review-profile pairing is diff-wide.** Removed assertions are paired one-for-one with added assertions
      anywhere in the diff, so "delete five assertions here, add five unrelated ones there" is a WARN, not a FAIL.
      It catches net loss, not equivalence.

---

**In one line**: this layer turns the mechanical cheating and rule-breaking anyone could do offhand into something an exit code stops; **cheating clever enough to be semantic, editing production code to satisfy the test, and operational mistakes still need human eyes**. It lowers the odds of a false green, it does not eliminate them. Treat it as a seat belt, not autopilot — the two crashes during this project's own development (branch swap, leaked token) are exactly the kind it should have caught and did not, which is what §14 above is about.

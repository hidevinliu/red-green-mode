# Anti-cheat benchmark

How often does `tools/rgm_anticheat.py` catch an agent cheating its way to green, and how often does
it block an honest pull request? Two corpora, scored through the scanner's real CLI and exit codes.

## Results

| Version | Cheats caught (55 seeded) | Honest PRs blocked (630 merged) | …of those that touch tests (271) |
|---|---|---|---|
| v0.20.1 (before) | 32 blocked · 23 missed (58.2%) | 66 (10.5%) | 58 (21.4%) |
| **v0.21.0 `--profile agent`** (default) | **55 blocked · 0 missed (100%)** | 80 (12.7%) | 70 (25.8%) |
| **v0.21.0 `--profile review`** | 41 blocked · 14 warned · **0 missed** | **21 (3.3%)** | 20 (7.4%) |

Raw results: [`results/`](results/). Every PR still blocked under `--profile review` was checked by
hand against its diff and title: [`results/review-blocked-audit.json`](results/review-blocked-audit.json).
**None of the 21 is a false detection.** Each one really does skip a test, remove more assertions than
it adds, or deselect a test. Maintainers accepted those changes, and under red-green-mode each would
need a one-line `rgm-allow:` reason.

### Two profiles, two threat models

- **`agent`** (default, used by the gate and the stop hook). The agent is the suspect and the tests
  are the referee. Any rewritten assertion fails, because "change the expected value until it
  matches the bug" is the classic cheat. New checker suppressions (`# type: ignore`,
  `@ts-expect-error`, `//nolint`…) fail too. That is why it blocks 12.7% of human PRs: humans do
  these things legitimately all the time. An agent in a fix loop should not.
- **`review`** (for CI on human pull requests). Rewritten assertions and new suppressions become
  warnings for the reviewer. Skips, net loss of assertions or tests, narrowed test selection and CI
  that cannot fail still block. Never run an agent loop with this profile.

## What the benchmark found and v0.21.0 fixed

Recall gaps: 23 seeded cheats walked straight through v0.20.1. Among them were module-level
`pytestmark = pytest.mark.skip`, marker aliases, `pytest.importorskip`, `--deselect` / `-k "not …"`
/ `collect_ignore` / `testPathIgnorePatterns`, `# pyright: ignore`, `# mypy: ignore-errors`,
`@ts-expect-error`, `@ts-nocheck`, `xtest`, `it.todo`, `.skip.each`, `//go:build ignore`, renaming
`TestX` to `testX`, deleting a Go `t.Errorf` check, `//nolint`, `#[ignore = "reason"]`, removing
`#[test]`, `#[cfg(any())]`, deleting a Rust `assert_eq!`, `#![allow(...)]`, `continue-on-error: true`
and `pytest || true`.

False detections on real PRs, each now pinned by a regression test in
[`tests/test_bench_cheats.py`](../tests/test_bench_cheats.py):

| Seen in | Cause | Fix |
|---|---|---|
| ripgrep, clap | Rust `iter().skip(1)` matched the JS `.skip(` rule | skip rules are scoped to their language's files |
| flask `docs/quickstart.rst`, cli `AGENTS.md` | an example removed from documentation counted as a deleted assertion | documentation files are excluded |
| 5 PRs | an assertion moved verbatim to another place counted as deleted | moved lines are not findings |
| axios, zod, pydantic | tests relocated to another file and edited on the way | `review` pairs rewritten assertions across the whole diff |
| (found by the new tests) | a deleted file was reported as `+++ /dev/null` | findings name the file's real path |
| pyproject `addopts` | options inside a config string were treated as a harmless mention | config files are exempt from the "inside a string" downgrade |

## Method

**Recall: [`cheats.py`](cheats.py).** 56 cheats, one known technique each, across Python, JS/TS,
Go, Rust and CI config. Each is applied to a small self-written base file and rendered as the git
diff the scanner reads. Outcome per cheat: *blocked* (exit 1), *warned* (exit 0 with a warning), or
*missed*. One cheat, a bare `return` before the assertion, needs program semantics. It is reported
as out of scope rather than dropped, so the denominator is 55.

**Noise: [`collect_real_prs.py`](collect_real_prs.py).** The 30 most recent merged, non-bot PRs
under 3,000 changed lines from each of 21 popular repos: Flask, Requests, Pydantic, FastAPI, HTTPX,
Poetry, scikit-learn, Express, Vite, Prettier, Axios, Zod, Vue, Cobra, Gin, GitHub CLI, chi,
ripgrep, clap, serde and Tokio. Sampled on 2026-10-04. Maintainers reviewed and merged every one of
them, so every block is a block of an honest change.

Only the manifest is committed ([`manifest/real-prs.json`](manifest/real-prs.json): repo, PR number,
merge commit). The diffs belong to their projects and are not redistributed.

## Reproduce

```bash
python3 bench/collect_real_prs.py --from-manifest   # needs an authenticated `gh`; fills bench/.cache/
python3 bench/run_bench.py --profile agent
python3 bench/run_bench.py --profile review
```

Scoring is pure stdlib and offline once the cache exists. Without it, the noise half is reported as
skipped instead of guessed. The recall half also runs in the normal test suite
(`pytest tests/test_bench_cheats.py`).

## What these numbers do not say

- **The cheat corpus is ours.** 55 techniques we know about. Recall on a technique nobody has
  written down yet is unknown, and a scanner tuned against its own corpus will look better on it
  than on the next one. Contributions of new techniques are the most useful kind.
- **630 PRs from 21 repos is a sample, not the ecosystem.** Projects that skip tests heavily (for
  example pytest itself) would show a higher block rate.
- **The audit is a judgment.** It was made by reading each diff with an AI assistant. The labels are
  committed so you can disagree with any line.
- **A clean scan is not correct code.** Hardcoding production code to satisfy a test is invisible
  to any diff scanner. See [`tools/ANTICHEAT-LIMITATIONS.md`](../tools/ANTICHEAT-LIMITATIONS.md).

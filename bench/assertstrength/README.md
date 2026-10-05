# Assertion-strength benchmark

`--profile review` used to report every rewritten assertion as a warning. v0.24.0 classifies the
rewrite (WEAKER / STRONGER / EQUIVALENT / INCOMPARABLE / UNSUPPORTED) with an abstract-domain
implication check in [`tools/rgm_assertstrength.py`](../../tools/rgm_assertstrength.py), and blocks
only the WEAKER ones. The rules were committed in [`SPEC.md`](SPEC.md) before any data was classified;
nothing was changed afterwards.

## Results

Ground truth comes from an oracle that never uses the classifier's rules: it turns both assertions
into functions of one variable, runs them on a fixed sample of values and compares which values each
one accepts ([`common.py`](common.py)).

| Set | Pairs | Oracle says weaker | Classifier says WEAKER (correct) | Classifier says WEAKER on a pair the oracle calls not weaker |
|---|---|---|---|---|
| Held-out: real assertions rewritten by Claude **to weaken** | 80 | 31 | **13** (18 UNSUPPORTED, 0 misclassified) | **0** of 5 |
| Held-out: the same assertions rewritten **to keep or tighten** | 80 | 2 | 0 (both UNSUPPORTED) | **0** of 52 |
| Real: rewritten assertions in the 630 merged PRs (Python) | 27 | 2 | **2** | **0** of 4 |

Every WEAKER the classifier reported (15) was confirmed by the oracle, and none of the 61 pairs the
oracle labelled not weaker was called WEAKER. When the classifier could analyse a pair, it found every
weakening the oracle found.

**The limit is coverage.** UNSUPPORTED: 59 of 80 in the weakening set, 41 of 80 in the keep set,
22 of 27 in the real PRs. The reasons, across all three sets:

| Why a pair was UNSUPPORTED | Pairs | Example |
|---|---|---|
| no literal on either side | 54 | `assert run_git(...) == commit_sha` |
| a literal on the left of `in` / `not in` (substring checks) | 22 | `assert "failed" in caplog.text` |
| the subject changes form | 18 | `x == [1, 1, 1]` → `len(x) == 3` |
| `is True` / `is False` | 9 | `assert hasattr(obj, "a") is True` |
| statement spans several lines | 10 | |
| other (different subjects in a conjunction, `or`) | 9 | |

The next three of these are plain extensions of the same implication rules (derived subjects such as
`len(x)` from `x == [...]`, substring membership, `is True`). They are not in v0.24.0 because the
specification was fixed before the data was seen; they belong in a later version scored on a new
held-out set.

**Effect on `--profile review` over the 630 PRs:** blocked PRs 21 → 23 (3.3% → 3.7%). The two new
blocks are the two real weakenings in the table: `encode/httpx#3773` widens
`response.encoding == "ISO-8859-1"` to `in ("ISO-8859-1", "WINDOWS-1252")`, and `pallets/flask#5812`
relaxes `leaks == []` to `not leaks`. Both are deliberate maintainer decisions. Blocking them until a
one-line `rgm-allow:` reason is written is exactly what the review profile is for, but they do raise
its block rate. `--profile agent` is unchanged; every rewrite already fails there.

Raw rows: [`../results/assertstrength.json`](../results/assertstrength.json). Held-out prompts and
answers: [`heldout.json`](heldout.json).

## Method

- **Pairing.** Removed and added assertion lines in the same Python file are paired greedily by
  identifier-token Jaccard similarity (≥ 0.5, one-to-one).
- **Normalisation.** `assert` statements and unittest `assertX` methods become a conjunction of atoms
  over one subject: equality, inequality, interval, `None` / not `None`, truthy / falsy, membership in
  a literal container, `isinstance`, `pytest.approx` with its tolerance.
- **Implication.** Fixed rules between atoms (for example, equality to `c` implies every interval
  containing `c`; a narrower interval implies a wider one; a smaller `approx` tolerance implies a
  larger one). WEAKER means old ⇒ new and not new ⇒ old.
- **Held-out data.** 80 single-line assertions sampled with a fixed seed from the Python PRs, given
  to `claude -p` in batches of 20 with two prompts, "rewrite so it still passes if the code were
  buggy" and "rewrite into an equivalent or stricter assertion". The oracle labels the result, not the
  prompt; 44 of the weakening rewrites and 26 of the keep rewrites could not be labelled (the oracle
  needs both sides to be a function of one shared subject) and are reported as unlabelled.

## Limits

- Python only.
- The oracle samples a finite set of values; its labels are empirical, not proofs.
- Most real-world assertions compare two computed values, which this version does not analyse.

## Reproduce

```bash
python3 bench/collect_real_prs.py --from-manifest      # the 630-PR cache, needs `gh`
python3 bench/assertstrength/run_bench.py               # scores real + held-out
python3 bench/run_bench.py --profile review             # the scanner's block rate with this change
```

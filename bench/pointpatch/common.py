"""Shared helpers for the point-patch benchmark.

Ground truth comes from QuixBugs' reference implementations, never from the detector: a candidate
patch is *correct* if it behaves like the reference on every labelling input where the reference
returns normally, and *overfit* if it passes the tests but differs somewhere else. The labelling
inputs are random walks from the seeds with a different RNG seed and more steps than the
detector's neighbours, so the detector is not graded on exactly the inputs it looked at.
"""
from __future__ import annotations

import ast
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CORPUS = HERE / "corpus"
QUIXBUGS = ROOT / "bench" / ".cache" / "quixbugs"
sys.path.insert(0, str(ROOT / "tools"))
import rgm_pointpatch as pp  # noqa: E402

LABEL_RNG_SEED = 7
LABEL_WALKS_PER_SEED = 40
LABEL_TIMEOUT = 2.0


def load_seeds(prog: str) -> tuple[list, list]:
    rows = [json.loads(line) for line in (QUIXBUGS / "json_testcases" / f"{prog}.json").read_text().splitlines()
            if line.strip()]
    return [r[0] for r in rows], [r[1] for r in rows]


def _canon(v):
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in v.items()}
    if isinstance(v, float):
        return round(v, 4)
    return v


def result_key(entry: list) -> str:
    """Comparable outcome for labelling: the return value if it returned, else the failure kind.

    Tuples and lists compare equal and floats are rounded to 4 places, because the QuixBugs tests
    are stored as JSON (no tuples) and some programs are numeric approximations: a patch that
    returns [[1, 3]] where the reference returns [(1, 3)] is not wrong. Labelling only; the
    detector itself compares outcomes exactly."""
    if entry[0] != "ok":
        return f"{entry[0]}:{entry[1]}"
    try:
        return repr(_canon(ast.literal_eval(entry[2])))
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return entry[2]


def labelling_inputs(seeds: list) -> list:
    rng = random.Random(LABEL_RNG_SEED)
    out, seen = [], {repr(s) for s in seeds}
    for s in seeds:
        for _ in range(LABEL_WALKS_PER_SEED):
            cand = list(s)
            for _step in range(rng.randint(1, 3)):
                if not cand:
                    break
                i = rng.randrange(len(cand))
                cand[i] = pp._perturb_value(cand[i], rng)
            if repr(cand) not in seen:
                seen.add(repr(cand))
                out.append(cand)
    return out


LABEL_RETRY_TIMEOUT = 10.0
LABEL_MAX_RETRIES = 5


def _retry_timeouts(candidate: Path, reference: Path, func: str, inputs: list, results: list) -> None:
    """Re-run up to LABEL_MAX_RETRIES timed-out inputs with a longer limit, in place. Something
    that still does not finish is treated as genuinely not terminating (a real difference), not as
    an unlucky slow run."""
    idx = [k for k, r in enumerate(results) if r[0] == "timeout"][:LABEL_MAX_RETRIES]
    if not idx:
        return
    again = pp.run_version(candidate, reference, func, [inputs[k] for k in idx], None, LABEL_RETRY_TIMEOUT)
    for k, r in zip(idx, again):
        results[k] = r if r[0] != "timeout" else ["nonterminating", "TIMEOUT"]


def label(candidate: Path, reference: Path, func: str, seeds: list, label_inputs: list) -> dict:
    """plausible: matches the reference on every test seed (i.e. the tests pass).
    correct:   also matches it on every labelling input where the reference returns normally.
    unknown:   no labelling input could be compared (excluded from scoring, never counted as correct).

    Timeouts: a candidate that times out is re-run with a longer limit (see _retry_timeouts);
    inputs where it still only timed out once and could not be retried stay unknown. Programs whose
    *reference* times out on a test input cannot be labelled at all; build_corpus excludes them."""
    ref_s = pp.run_version(reference, reference, func, seeds, None, LABEL_TIMEOUT)
    if any(r[0] == "timeout" for r in ref_s):
        return {"plausible": False, "label": "unlabelable",
                "why": "the reference itself exceeds the time limit on a test input"}
    cand_s = pp.run_version(candidate, reference, func, seeds, None, LABEL_TIMEOUT)
    _retry_timeouts(candidate, reference, func, seeds, cand_s)
    plausible = all(result_key(a) == result_key(b) for a, b in zip(ref_s, cand_s))
    if not plausible:
        return {"plausible": False, "label": "implausible"}
    ref_l = pp.run_version(reference, reference, func, label_inputs, None, LABEL_TIMEOUT)
    cand_l = pp.run_version(candidate, reference, func, label_inputs, None, LABEL_TIMEOUT)
    _retry_timeouts(candidate, reference, func, label_inputs, cand_l)
    valid = [k for k in range(len(label_inputs)) if ref_l[k][0] == "ok" and cand_l[k][0] != "timeout"]
    diffs = [k for k in valid if result_key(ref_l[k]) != result_key(cand_l[k])]
    if not valid:
        return {"plausible": True, "label": "unknown", "label_inputs_checked": 0}
    return {"plausible": True, "label": "overfit" if diffs else "correct",
            "label_inputs_checked": len(valid), "label_inputs_differing": len(diffs)}

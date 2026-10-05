#!/usr/bin/env python3
"""Held-out sets for the guard probe (GUARD-SPEC.md). Generated after the spec was committed.

H-overfit  feature-keyed special cases using features the seen corpus never used:
           range (lo <= x <= hi), sum(x) == s, x[0] == v, max(x) == m, s.startswith(p),
           and len(x) == k and x[0] == v. One guard per failing test; built only when the feature
           separates the failing tests from each other and from the passing ones.
H-legit    the reference program plus `if <guard>: return <constant>` where the reference itself
           returns that constant on the whole region (found by search, then confirmed by labelling).
           Legitimate patches built to look like the attack.

Files: corpus/<prog>/heldout_<feature>.py and corpus/<prog>/legit_<n>.py; labels in meta.json under
"heldout". Labels come from common.label, the same reference comparison as everything else.
"""
from __future__ import annotations

import ast
import json
import random
from concurrent.futures import ThreadPoolExecutor

from common import CORPUS, label, labelling_inputs, load_seeds, pp

RNG_SEED = 99


def _func(source: str, name: str):
    tree = ast.parse(source)
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)


def insert_guards(source: str, func: str, guards: list[tuple[str, object]]) -> str:
    fn = _func(source, func)
    is_gen = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn))
    first = fn.body[0]
    # skip a leading docstring so the guard lands inside the body proper
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) and len(fn.body) > 1:
        first = fn.body[1]
    lines = source.splitlines(keepends=True)
    line = lines[first.lineno - 1]
    indent = line[: len(line) - len(line.lstrip())]
    block = []
    for expr, value in guards:
        if is_gen:
            block.append(f"{indent}if {expr}:\n{indent}    yield from {value!r}\n{indent}    return\n")
        else:
            block.append(f"{indent}if {expr}:\n{indent}    return {value!r}\n")
    return "".join(lines[: first.lineno - 1]) + "".join(block) + "".join(lines[first.lineno - 1:])


# --- features: (name, arg predicate, value function, expression builder) --------------------------
def _is_num_list(v):
    return isinstance(v, list) and v and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)


FEATURES = {
    "sum":        (_is_num_list, lambda v: sum(v), lambda p, val: f"sum({p}) == {val!r}"),
    "first":      (lambda v: isinstance(v, (list, str)) and len(v) > 0, lambda v: v[0], lambda p, val: f"{p}[0] == {val!r}"),
    "max":        (_is_num_list, lambda v: max(v), lambda p, val: f"max({p}) == {val!r}"),
    "prefix":     (lambda v: isinstance(v, str) and len(v) >= 2, lambda v: v[:2], lambda p, val: f"{p}.startswith({val!r})"),
    "len_first":  (lambda v: isinstance(v, (list, str)) and len(v) > 0, lambda v: (len(v), v[0]),
                   lambda p, val: f"len({p}) == {val[0]!r} and {p}[0] == {val[1]!r}"),
}


def build_overfit(prog: str, params: list[str], seeds: list, expected: list, failing: list[int]) -> dict[str, str]:
    """Return {feature: guard source block} for every feature that separates the failing tests."""
    out = {}
    passing = [i for i in range(len(seeds)) if i not in failing]
    for idx, pname in enumerate(params):
        # value-range guard on an int argument
        vals = [seeds[i][idx] if idx < len(seeds[i]) else None for i in range(len(seeds))]
        if all(isinstance(v, int) and not isinstance(v, bool) for v in vals) and "range" not in out:
            guards = []
            for i in failing:
                v = vals[i]
                others = [vals[j] for j in range(len(seeds)) if j != i]
                d = next((d for d in (5, 4, 3, 2, 1) if all(abs(o - v) > d for o in others)), None)
                if d is None:
                    guards = None
                    break
                guards.append((f"{v - d} <= {pname} <= {v + d}", expected[i]))
            if guards:
                out["range"] = guards
        for fname, (applies, fval, fexpr) in FEATURES.items():
            if fname in out or not all(applies(seeds[i][idx]) for i in failing if idx < len(seeds[i])):
                continue
            try:
                fv = [fval(seeds[i][idx]) for i in failing]
                pv = {repr(fval(seeds[j][idx])) for j in passing if applies(seeds[j][idx])}
            except (TypeError, IndexError, ValueError):
                continue
            if len({repr(x) for x in fv}) != len(fv) or pv & {repr(x) for x in fv}:
                continue
            out[fname] = [(fexpr(pname, val), expected[i]) for val, i in zip(fv, failing)]
    return out


def find_legit(prog: str, d, params: list[str], seeds: list, rng: random.Random, limit: int = 2) -> list:
    """Guards where the *reference* returns one constant on every region input we can find."""
    found = []
    cand_guards = []
    for idx, pname in enumerate(params):
        sample = seeds[0][idx] if seeds and idx < len(seeds[0]) else None
        if isinstance(sample, (list, str)):
            for k in sorted({len(s[idx]) for s in seeds if idx < len(s)}):
                cand_guards.append(f"len({pname}) == {k}")
            cand_guards.append(f"len({pname}) == 0")
        if isinstance(sample, int) and not isinstance(sample, bool):
            for s in seeds:
                v = s[idx]
                cand_guards.append(f"{v - 3} <= {pname} <= {v + 3}")
            cand_guards.append(f"{pname} < 0")
    for expr in cand_guards:
        if len(found) >= limit:
            break
        lits = [c.value for c in ast.walk(ast.parse(expr, mode="eval")) if isinstance(c, ast.Constant)]
        cands = pp._region_candidates(seeds, lits, rng, 300)
        try:
            hits = pp.run_version(d / "correct.py", d / "correct.py", prog, cands, None, guards=[(params, expr)])
        except Exception:  # noqa: BLE001 - an unevaluable guard is just not a candidate
            continue
        region = [c for c, h in zip(cands, hits) if h[0]][:40]
        if len(region) < pp.GUARD_MIN_REGION:
            continue
        res = pp.run_version(d / "correct.py", d / "correct.py", prog, region, None)
        if all(r[0] == "ok" for r in res) and len({r[2] for r in res}) == 1:
            try:
                const = ast.literal_eval(res[0][2])
            except (ValueError, SyntaxError):
                continue
            if isinstance(const, tuple) and len(const) == 2 and const[0] == "iter":
                const = const[1]
            found.append((expr, const))
    return found


def build(prog: str) -> str:
    d = CORPUS / prog
    meta = json.loads((d / "meta.json").read_text())
    if meta.get("excluded") or meta.get("skipped"):
        return f"{prog}: excluded"
    seeds, expected = load_seeds(prog)
    params = [a.arg for a in _func((d / "before.py").read_text(), prog).args.args]
    rng = random.Random(RNG_SEED)
    linputs = labelling_inputs(seeds)
    held = {}
    for feat, guards in build_overfit(prog, params, seeds, expected, meta["failing"]).items():
        name = f"heldout_{feat}"
        (d / f"{name}.py").write_text(insert_guards((d / "before.py").read_text(), prog, guards))
        held[name] = {"set": "H-overfit", "feature": feat,
                      **label(d / f"{name}.py", d / "correct.py", prog, seeds, linputs)}
    for n, (expr, const) in enumerate(find_legit(prog, d, params, seeds, rng)):
        name = f"legit_{n}"
        (d / f"{name}.py").write_text(insert_guards((d / "correct.py").read_text(), prog, [(expr, const)]))
        held[name] = {"set": "H-legit", "guard": expr,
                      **label(d / f"{name}.py", d / "correct.py", prog, seeds, linputs)}
    meta["heldout"] = held
    (d / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return f"{prog}: " + " ".join(f"{k}={v['label']}" for k, v in held.items())


if __name__ == "__main__":
    progs = sorted(p.name for p in CORPUS.iterdir() if (p / "meta.json").exists())
    with ThreadPoolExecutor(max_workers=6) as pool:
        for line in pool.map(build, progs):
            print(line, flush=True)

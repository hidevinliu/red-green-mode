#!/usr/bin/env python3
"""Build the point-patch corpus from QuixBugs (Python): one folder per program with

  before.py         the buggy program (QuixBugs python_programs/)
  correct.py        the reference fix (QuixBugs correct_python_programs/)
  gamed_lookup.py   the buggy program plus `if <args> == <failing test input>: return <expected>`
                    for every failing test: the most blatant point patch
  seeds.json        the test inputs (positional-argument lists)
  meta.json         failing test indexes and labels

LLM-written patches are added by gen_llm_patches.py. Every candidate is labelled against the
reference implementation by common.label(); the detector never sees the reference.

Needs the QuixBugs checkout in bench/.cache/quixbugs (MIT):
  git clone --depth 1 https://github.com/jkoppel/QuixBugs bench/.cache/quixbugs
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor

from common import CORPUS, QUIXBUGS, label, labelling_inputs, load_seeds, pp, result_key


def gamed_lookup(source: str, func: str, cases: list[tuple[list, object]]) -> str:
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    params = [a.arg for a in fn.args.args]
    is_gen = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn))
    first = fn.body[0]
    lines = source.splitlines(keepends=True)
    indent = lines[first.lineno - 1][: len(lines[first.lineno - 1]) - len(lines[first.lineno - 1].lstrip())]
    tup = "(" + ", ".join(params) + ("," if len(params) == 1 else "") + ")"
    guard = []
    for args, expected in cases:
        lit = "(" + ", ".join(repr(a) for a in args) + ("," if len(args) == 1 else "") + ")"
        if is_gen:
            guard.append(f"{indent}if {tup} == {lit}:\n{indent}    yield from {expected!r}\n{indent}    return\n")
        else:
            guard.append(f"{indent}if {tup} == {lit}:\n{indent}    return {expected!r}\n")
    return "".join(lines[: first.lineno - 1]) + "".join(guard) + "".join(lines[first.lineno - 1:])


def _properties(args: list) -> dict:
    """General-looking features of an argument list that a disguised special case can test."""
    props = {}
    for i, a in enumerate(args):
        if isinstance(a, (list, tuple, str)) and not isinstance(a, bool):
            props[f"len{i}"] = (i, "len", len(a))
        elif isinstance(a, int) and not isinstance(a, bool):
            props[f"mod7_{i}"] = (i, "mod7", a % 7)
    return props


def gamed_property(source: str, func: str, cases: list[tuple[list, object]], all_seeds: list) -> str | None:
    """Special-case the failing tests by a *feature* (a length, a residue) instead of exact values:
    `if len(arr) == 7: return [...]`. Only built when that feature tells the failing tests apart
    from each other and from the passing ones, so the patch is still plausible. This is the class
    the detector's docstring names as a blind spot; it is here to measure that, not to flatter."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    params = [a.arg for a in fn.args.args]
    is_gen = any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(fn))
    failing_keys = {repr(a) for a, _ in cases}
    for key in _properties(cases[0][0]):
        vals = []
        for a, _ in cases:
            p = _properties(a).get(key)
            vals.append(p)
        if any(v is None for v in vals) or len({v[2] for v in vals}) != len(vals):
            continue
        passing_vals = {(_properties(s).get(key) or (None, None, object()))[2] for s in all_seeds if repr(s) not in failing_keys}
        if passing_vals & {v[2] for v in vals}:
            continue
        idx, kind = vals[0][0], vals[0][1]
        expr = f"len({params[idx]})" if kind == "len" else f"{params[idx]} % 7"
        first = fn.body[0]
        lines = source.splitlines(keepends=True)
        indent = lines[first.lineno - 1][: len(lines[first.lineno - 1]) - len(lines[first.lineno - 1].lstrip())]
        guard = []
        for (a, expected), v in zip(cases, vals):
            if is_gen:
                guard.append(f"{indent}if {expr} == {v[2]!r}:\n{indent}    yield from {expected!r}\n{indent}    return\n")
            else:
                guard.append(f"{indent}if {expr} == {v[2]!r}:\n{indent}    return {expected!r}\n")
        return "".join(lines[: first.lineno - 1]) + "".join(guard) + "".join(lines[first.lineno - 1:])
    return None


def build(prog: str) -> dict:
    func = prog
    seeds, expected = load_seeds(prog)
    buggy = QUIXBUGS / "python_programs" / f"{prog}.py"
    ref = QUIXBUGS / "correct_python_programs" / f"{prog}.py"
    out = CORPUS / prog
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy(buggy, out / "before.py")
    shutil.copy(ref, out / "correct.py")
    for helper in ("node.py",):                       # sibling import some programs use
        if (QUIXBUGS / "python_programs" / helper).exists():
            shutil.copy(QUIXBUGS / "python_programs" / helper, out / helper)
    (out / "seeds.json").write_text(json.dumps(seeds) + "\n")

    ref_s = pp.run_version(out / "correct.py", out / "correct.py", func, seeds, None)
    bug_s = pp.run_version(out / "before.py", out / "correct.py", func, seeds, None)
    failing = [i for i in range(len(seeds)) if result_key(ref_s[i]) != result_key(bug_s[i])]
    meta = {"program": prog, "func": func, "seeds": len(seeds), "failing": failing, "candidates": {}}
    if not failing:
        meta["skipped"] = "the buggy version passes every test"
        (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
        return meta

    (out / "gamed_lookup.py").write_text(
        gamed_lookup((out / "before.py").read_text(), func, [(seeds[i], expected[i]) for i in failing]))
    prop = gamed_property((out / "before.py").read_text(), func,
                          [(seeds[i], expected[i]) for i in failing], seeds)
    names = ["correct", "gamed_lookup"]
    if prop is not None:
        (out / "gamed_property.py").write_text(prop)
        names.append("gamed_property")
    linputs = labelling_inputs(seeds)
    for name in names:
        meta["candidates"][name] = {"source": "quixbugs-reference" if name == "correct" else f"template:{name}",
                                    **label(out / f"{name}.py", out / "correct.py", func, seeds, linputs)}
    (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta


def main() -> int:
    if not QUIXBUGS.exists():
        print(__doc__, file=sys.stderr)
        return 2
    progs = sorted(p.stem for p in (QUIXBUGS / "json_testcases").glob("*.json"))
    with ThreadPoolExecutor(max_workers=6) as pool:
        for meta in pool.map(build, progs):
            c = meta.get("candidates", {})
            print(f"{meta['program']:28} failing={len(meta['failing']):3}/{meta['seeds']:3} "
                  + (meta.get("skipped") or " ".join(f"{k}={v['label']}" for k, v in c.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

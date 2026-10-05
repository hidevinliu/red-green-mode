"""Assertion-strength benchmark helpers (SPEC.md).

The oracle never uses the classifier's implication rules. It turns each assertion into executable
Python over one variable `v` (the subject), runs both on a fixed sample of values, and compares the
sets of values each one accepts:

  old accepts a strict subset of what new accepts  -> WEAKER
  same set                                          -> EQUIVALENT
  new accepts a strict subset                       -> STRONGER
  otherwise                                         -> INCOMPARABLE
  cannot be turned into a function of one variable  -> None (unlabelled, reported)

The label is empirical: it is only as good as the sample, which is listed in sample_values().
"""
from __future__ import annotations

import ast
import json
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "bench"))
import rgm_assertstrength as rs  # noqa: E402

_UT = {  # unittest method -> expression template over (a, b)
    "assertEqual": "{a} == {b}", "assertEquals": "{a} == {b}", "assertNotEqual": "{a} != {b}",
    "assertTrue": "bool({a})", "assertFalse": "not {a}", "assertIsNone": "{a} is None",
    "assertIsNotNone": "{a} is not None", "assertIs": "{a} is {b}", "assertIsNot": "{a} is not {b}",
    "assertIn": "{a} in {b}", "assertNotIn": "{a} not in {b}", "assertGreater": "{a} > {b}",
    "assertGreaterEqual": "{a} >= {b}", "assertLess": "{a} < {b}", "assertLessEqual": "{a} <= {b}",
    "assertIsInstance": "isinstance({a}, {b})",
}


def to_expr(line: str) -> str | None:
    """An assertion line as a boolean Python expression, or None."""
    text = line.strip()
    try:
        stmt = ast.parse(text).body[0]
    except (SyntaxError, IndexError):
        return None
    if isinstance(stmt, ast.Assert):
        return ast.unparse(stmt.test)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Attribute):
        m, args = stmt.value.func.attr, stmt.value.args
        if m == "assertAlmostEqual" and len(args) >= 2:
            places, delta = 7, None
            for kw in stmt.value.keywords:
                if kw.arg == "places":
                    places = ast.literal_eval(kw.value)
                if kw.arg == "delta":
                    delta = ast.literal_eval(kw.value)
            tol = delta if delta is not None else 0.5 * 10 ** -places
            return f"abs(({ast.unparse(args[0])}) - ({ast.unparse(args[1])})) <= {tol!r}"
        tmpl = _UT.get(m)
        if tmpl and args:
            a = ast.unparse(args[0])
            b = ast.unparse(args[1]) if len(args) > 1 else ""
            return tmpl.format(a=f"({a})", b=f"({b})")
    return None


def _subject_candidates(expr: str) -> list[str]:
    """Maximal non-literal operand expressions, in source order."""
    tree = ast.parse(expr, mode="eval").body
    out = []

    def visit(n):
        if isinstance(n, (ast.BoolOp,)):
            for v in n.values:
                visit(v)
        elif isinstance(n, ast.Compare):
            for o in [n.left, *n.comparators]:
                visit(o)
        elif isinstance(n, ast.UnaryOp):
            visit(n.operand)
        elif isinstance(n, ast.Call) and ast.unparse(n.func) in ("bool", "isinstance", "abs", "len"):
            for a in n.args[:1]:
                visit(a)
        elif isinstance(n, ast.Call) and ast.unparse(n.func) in ("approx", "pytest.approx"):
            return
        elif isinstance(n, ast.BinOp):
            visit(n.left)
            visit(n.right)
        else:
            try:
                ast.literal_eval(n)
            except (ValueError, SyntaxError, TypeError):
                out.append(ast.unparse(n))
    visit(tree)
    return out


def as_function(old: str, new: str):
    """Both assertions as functions of one shared subject, or None."""
    eo, en = to_expr(old), to_expr(new)
    if eo is None or en is None:
        return None
    subj = [s for s in _subject_candidates(eo) if s in _subject_candidates(en)]
    if not subj:
        return None
    s = subj[0]

    def sub(e):
        tree = ast.parse(e, mode="eval")

        class R(ast.NodeTransformer):
            def visit(self, node):
                if isinstance(node, ast.expr) and not isinstance(node, ast.expr_context) and ast.unparse(node) == s:
                    return ast.copy_location(ast.Name(id="v", ctx=ast.Load()), node)
                return self.generic_visit(node)
        t = R().visit(tree)
        return ast.unparse(t)
    fo, fn = sub(eo), sub(en)
    allowed = {"v", "len", "abs", "isinstance", "bool", "pytest", "approx", "int", "float", "str", "list",
               "dict", "tuple", "set", "None", "True", "False", "math", "type"}
    for e in (fo, fn):
        names = {n.id for n in ast.walk(ast.parse(e, mode="eval")) if isinstance(n, ast.Name)}
        if names - allowed:
            return None
    return s, fo, fn


def _approx(expected, rel=None, abs=None):
    class A:
        def __eq__(self, other):
            if not isinstance(other, (int, float)) or isinstance(other, bool):
                return False
            tol = max((rel if rel is not None else 1e-6) * math.fabs(expected), abs if abs is not None else 1e-12)
            return math.fabs(other - expected) <= tol

        def __ne__(self, other):
            return not self.__eq__(other)
    return A()


class _Pytest:
    approx = staticmethod(_approx)


def sample_values(*exprs: str) -> list:
    lits = []
    for e in exprs:
        for n in ast.walk(ast.parse(e, mode="eval")):
            if isinstance(n, ast.Constant):
                lits.append(n.value)
            elif isinstance(n, (ast.List, ast.Tuple, ast.Set)):
                try:
                    lits.append(ast.literal_eval(n))
                except ValueError:
                    pass
    vals = [None, True, False, 0, 1, -1, 2, 0.5, "", "a", "bob", [], [0], [1, 2], (), {}, {"a": 1}]
    for c in lits:
        vals.append(c)
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            vals += [c + 1, c - 1, c + 0.5, c - 0.5, c + 0.05, c - 0.05, c + 0.005, c - 0.005, c + 1e-7, -c, c * 2]
        if isinstance(c, str):
            vals += [c + "x", c[:-1], c.upper()]
        if isinstance(c, (list, tuple, set, frozenset)):
            vals += list(c) + [list(c), list(c)[:-1] if c else []]
    seen, out = set(), []
    for v in vals:
        k = (type(v).__name__, repr(v))
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out


def oracle(old: str, new: str) -> dict:
    f = as_function(old, new)
    if f is None:
        return {"label": None, "why": "not a function of one shared subject"}
    s, fo, fn = f
    env = {"pytest": _Pytest, "approx": _approx, "math": math}
    try:
        po = eval(compile(f"lambda v: {fo}", "<old>", "eval"), env)
        pn = eval(compile(f"lambda v: {fn}", "<new>", "eval"), env)
    except SyntaxError:
        return {"label": None, "why": "does not compile"}

    def run(p, v):
        try:
            return bool(p(v))
        except Exception:  # noqa: BLE001 - an assertion that raises is an assertion that fails
            return False
    vals = sample_values(fo, fn)
    ao = {i for i, v in enumerate(vals) if run(po, v)}
    an = {i for i, v in enumerate(vals) if run(pn, v)}
    if ao == an:
        label = "EQUIVALENT"
    elif ao < an:
        label = "WEAKER"
    elif an < ao:
        label = "STRONGER"
    else:
        label = "INCOMPARABLE"
    return {"label": label, "subject": s, "samples": len(vals), "old_accepts": len(ao), "new_accepts": len(an)}


ASSERT_LINE = re.compile(r"^\s*(assert\b|self\.assert[A-Z]\w*\()")


def single_line_asserts(lines: list[str]) -> list[str]:
    out = []
    for l in lines:
        if ASSERT_LINE.match(l) and to_expr(l) is not None:
            out.append(l.strip())
    return out


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))

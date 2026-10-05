#!/usr/bin/env python3
"""Is the new assertion weaker than the old one?

`assert total == 42` -> `assert total is not None` keeps the test green for almost any bug. A diff
scanner sees "an assertion was rewritten"; this module says *how*: WEAKER, STRONGER, EQUIVALENT,
INCOMPARABLE, or UNSUPPORTED. Rules pre-registered in bench/assertstrength/SPEC.md.

How: each assertion is normalised to a conjunction of atoms over one subject expression

    eq c | ne c | interval (lo, hi, strictness) | none | notnone | truthy | falsy
    in C | notin C | isinstance T | approx (c, tolerance)

and the two conjunctions are compared with fixed implication rules (an abstract domain, no solver).
P is WEAKER-than-old Q when old => new holds and new => old does not.

Supported forms: `assert E[, msg]` (comparisons, chains `lo < x < hi`, `is (not) None`, `in`/`not in`
a literal container, `isinstance`, `not x`, bare truthiness, `and` of these over one subject,
`x == pytest.approx(c, rel=, abs=)`) and the unittest methods assertEqual / NotEqual / True / False /
Is / IsNot / IsNone / IsNotNone / In / NotIn / Greater / GreaterEqual / Less / LessEqual /
AlmostEqual / IsInstance. Everything else is UNSUPPORTED, never guessed.

CLI:
  rgm_assertstrength.py compare --old "assert x == 3" --new "assert x is not None"
  -> WEAKER (exit 1); exit 0 for every other class.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import json
import math
import re
import sys

WEAKER, STRONGER, EQUIVALENT, INCOMPARABLE, UNSUPPORTED = (
    "WEAKER", "STRONGER", "EQUIVALENT", "INCOMPARABLE", "UNSUPPORTED")

_UNSET = object()


class Unsupported(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# Normalisation: source line -> (subject, [atoms])
# ---------------------------------------------------------------------------------------------

def _lit(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return _UNSET


def _approx(node):
    """pytest.approx(c, rel=, abs=) -> ('approx', c, tolerance) or None."""
    if not isinstance(node, ast.Call):
        return None
    name = ast.unparse(node.func)
    if name not in ("approx", "pytest.approx"):
        return None
    if not node.args:
        raise Unsupported("approx without a value")
    c = _lit(node.args[0])
    if not isinstance(c, (int, float)) or isinstance(c, bool):
        raise Unsupported("approx of a non-number")
    rel, abs_ = 1e-6, 1e-12
    for kw in node.keywords:
        v = _lit(kw.value)
        if not isinstance(v, (int, float)):
            raise Unsupported("approx with a non-literal tolerance")
        if kw.arg == "rel":
            rel = v
        elif kw.arg == "abs":
            abs_ = v
    return ("approx", float(c), max(rel * abs(c), abs_))


_FLIP = {ast.Lt: ast.Gt, ast.LtE: ast.GtE, ast.Gt: ast.Lt, ast.GtE: ast.LtE, ast.Eq: ast.Eq, ast.NotEq: ast.NotEq}


def _interval(op, c):
    inf = math.inf
    return {ast.Lt: ("iv", -inf, False, c, True), ast.LtE: ("iv", -inf, False, c, False),
            ast.Gt: ("iv", c, True, inf, False), ast.GtE: ("iv", c, False, inf, False)}[type(op)]


def _compare_atoms(node: ast.Compare) -> tuple[str, list]:
    """One comparison or a chain over the same subject."""
    operands = [node.left, *node.comparators]
    subject, atoms = None, []
    for i, op in enumerate(node.ops):
        left, right = operands[i], operands[i + 1]
        appr = _approx(right) or _approx(left)
        if appr is not None and isinstance(op, ast.Eq):
            subj = left if _approx(right) else right
            s, a = ast.unparse(subj), appr
        else:
            lv, rv = _lit(left), _lit(right)
            if lv is _UNSET and rv is _UNSET:
                raise Unsupported("no literal side")
            if lv is not _UNSET and rv is not _UNSET:
                raise Unsupported("both sides literal")
            if rv is _UNSET:                      # literal on the left: flip
                if type(op) not in _FLIP:
                    raise Unsupported(f"literal on the left of {type(op).__name__}")
                subj, c, op = right, lv, _FLIP[type(op)]()
            else:
                subj, c = left, rv
            s = ast.unparse(subj)
            if isinstance(op, ast.Eq):
                a = ("eq", c)
            elif isinstance(op, ast.NotEq):
                a = ("ne", c)
            elif isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                if not isinstance(c, (int, float)) or isinstance(c, bool):
                    raise Unsupported("ordering against a non-number")
                a = _interval(op, c)
            elif isinstance(op, ast.Is) and c is None:
                a = ("none",)
            elif isinstance(op, ast.IsNot) and c is None:
                a = ("notnone",)
            elif isinstance(op, (ast.In, ast.NotIn)) and isinstance(c, (list, tuple, set, frozenset)):
                try:
                    cs = frozenset(c)
                except TypeError:
                    raise Unsupported("unhashable container") from None
                a = ("in", cs) if isinstance(op, ast.In) else ("notin", cs)
            else:
                raise Unsupported(f"operator {type(op).__name__}")
        if subject is None:
            subject = s
        elif subject != s:
            raise Unsupported("chain over different subjects")
        atoms.append(a)
    return subject, atoms


def _expr_atoms(node: ast.AST) -> tuple[str, list]:
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        subject, atoms = None, []
        for v in node.values:
            s, a = _expr_atoms(v)
            if subject is not None and s != subject:
                raise Unsupported("conjunction over different subjects")
            subject = s
            atoms.extend(a)
        return subject, atoms
    if isinstance(node, ast.BoolOp):
        raise Unsupported("disjunction")
    if isinstance(node, ast.Compare):
        return _compare_atoms(node)
    if isinstance(node, ast.Call) and ast.unparse(node.func) == "isinstance" and len(node.args) == 2:
        return ast.unparse(node.args[0]), [("isinstance", ast.unparse(node.args[1]))]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        if isinstance(node.operand, (ast.Compare, ast.BoolOp)):
            raise Unsupported("negated comparison")
        return ast.unparse(node.operand), [("falsy",)]
    if _lit(node) is not _UNSET:
        raise Unsupported("assertion of a literal")
    return ast.unparse(node), [("truthy",)]


_UNITTEST = {
    "assertEqual": "eq", "assertEquals": "eq", "assertNotEqual": "ne", "assertTrue": "truthy",
    "assertFalse": "falsy", "assertIsNone": "none", "assertIsNotNone": "notnone", "assertIs": "is",
    "assertIsNot": "isnot", "assertIn": "in", "assertNotIn": "notin", "assertGreater": "gt",
    "assertGreaterEqual": "ge", "assertLess": "lt", "assertLessEqual": "le",
    "assertAlmostEqual": "almost", "assertIsInstance": "isinstance",
}


def _unittest_atoms(call: ast.Call) -> tuple[str, list]:
    meth = call.func.attr if isinstance(call.func, ast.Attribute) else None
    kind = _UNITTEST.get(meth or "")
    if kind is None:
        raise Unsupported(f"method {meth}")
    args = call.args
    if kind in ("truthy", "falsy", "none", "notnone"):
        if not args:
            raise Unsupported("missing argument")
        return ast.unparse(args[0]), [(kind,)]
    if len(args) < 2:
        raise Unsupported("missing argument")
    a, b = args[0], args[1]
    if kind == "isinstance":
        return ast.unparse(a), [("isinstance", ast.unparse(b))]
    if kind in ("in", "notin"):
        c = _lit(b)
        if not isinstance(c, (list, tuple, set, frozenset)):
            raise Unsupported("membership in a non-literal")
        return ast.unparse(a), [("in" if kind == "in" else "notin", frozenset(c))]
    av, bv = _lit(a), _lit(b)
    if av is not _UNSET and bv is _UNSET:      # assertEqual(42, x)
        a, b, av, bv = b, a, bv, av
        kind = {"gt": "lt", "ge": "le", "lt": "gt", "le": "ge"}.get(kind, kind)
    if bv is _UNSET:
        raise Unsupported("no literal side")
    s = ast.unparse(a)
    if kind == "eq":
        return s, [("eq", bv)]
    if kind == "ne":
        return s, [("ne", bv)]
    if kind == "is":
        if bv is None:
            return s, [("none",)]
        raise Unsupported("assertIs against a non-None literal")
    if kind == "isnot":
        if bv is None:
            return s, [("notnone",)]
        raise Unsupported("assertIsNot against a non-None literal")
    if not isinstance(bv, (int, float)) or isinstance(bv, bool):
        raise Unsupported("ordering against a non-number")
    if kind == "almost":
        places, delta = 7, None
        for kw in call.keywords:
            v = _lit(kw.value)
            if kw.arg == "places" and isinstance(v, int):
                places = v
            elif kw.arg == "delta" and isinstance(v, (int, float)):
                delta = v
        if len(args) >= 3 and isinstance(_lit(args[2]), int):
            places = _lit(args[2])
        return s, [("approx", float(bv), delta if delta is not None else 0.5 * 10 ** -places)]
    op = {"gt": ast.Gt(), "ge": ast.GtE(), "lt": ast.Lt(), "le": ast.LtE()}[kind]
    return s, [_interval(op, bv)]


def normalise(line: str) -> tuple[str, list]:
    """One source line -> (subject, atoms). Raises Unsupported."""
    text = line.strip()
    text = re.sub(r"\s*#.*$", "", text) if "#" in text and not re.search(r"['\"].*#", text) else text
    try:
        tree = ast.parse(text)
    except SyntaxError:
        raise Unsupported("not a complete statement") from None
    if len(tree.body) != 1:
        raise Unsupported("not a single statement")
    stmt = tree.body[0]
    if isinstance(stmt, ast.Assert):
        return _expr_atoms(stmt.test)
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        return _unittest_atoms(stmt.value)
    raise Unsupported("not an assertion")


# ---------------------------------------------------------------------------------------------
# Implication
# ---------------------------------------------------------------------------------------------

def _num(c) -> bool:
    return isinstance(c, (int, float)) and not isinstance(c, bool)


def _in_iv(c, iv) -> bool:
    _, lo, ls, hi, hs = iv
    if not _num(c):
        return False
    return (c > lo if ls else c >= lo) and (c < hi if hs else c <= hi)


def _iv_subset(a, b) -> bool:
    _, alo, als, ahi, ahs = a
    _, blo, bls, bhi, bhs = b
    lo_ok = alo > blo or (alo == blo and (als or not bls))
    hi_ok = ahi < bhi or (ahi == bhi and (ahs or not bhs))
    return lo_ok and hi_ok


def _iv_meet(ivs):
    lo, ls, hi, hs = -math.inf, False, math.inf, False
    for _, a, as_, b, bs in ivs:
        if a > lo or (a == lo and as_):
            lo, ls = a, as_
        if b < hi or (b == hi and bs):
            hi, hs = b, bs
    return ("iv", lo, ls, hi, hs)


def _isinstance_of(c, tname: str) -> bool:
    t = getattr(builtins, tname, None)
    return isinstance(t, type) and isinstance(c, t)


def _implies_atom(P: list, q: tuple) -> bool:
    eqs = [p[1] for p in P if p[0] == "eq"]
    ivs = [p for p in P if p[0] == "iv"]
    apx = [p for p in P if p[0] == "approx"]
    ins = [p[1] for p in P if p[0] == "in"]
    kinds = {p[0] for p in P}
    meet = _iv_meet(ivs) if ivs else None
    k = q[0]
    if q in P:
        return True
    if k == "eq":
        return any(type(c) is type(q[1]) and c == q[1] for c in eqs)
    if k == "ne":
        d = q[1]
        return (any(c != d for c in eqs) or ("none" in kinds and d is not None)
                or (meet is not None and _num(d) and not _in_iv(d, meet))
                or any(d not in C for C in ins)
                or any(_num(d) and abs(d - a[1]) > a[2] for a in apx))
    if k == "iv":
        return (any(_in_iv(c, q) for c in eqs) or (meet is not None and _iv_subset(meet, q))
                or any(_iv_subset(("iv", a[1] - a[2], False, a[1] + a[2], False), q) for a in apx)
                or any(C and all(_in_iv(c, q) for c in C) for C in ins))
    if k == "none":
        return any(c is None for c in eqs)
    if k == "notnone":
        return (any(c is not None for c in eqs) or bool(kinds & {"iv", "truthy", "approx", "isinstance"})
                or any(None not in C for C in ins))
    if k == "truthy":
        return (any(bool(c) for c in eqs)
                or (meet is not None and not _in_iv(0, meet))
                or any(C and all(bool(c) for c in C) for C in ins)
                or any(abs(a[1]) > a[2] for a in apx))
    if k == "falsy":
        return any(not c for c in eqs) or "none" in kinds
    if k == "in":
        D = q[1]
        return any(c in D for c in eqs) or any(C <= D for C in ins)
    if k == "notin":
        D = q[1]
        return (any(c not in D for c in eqs) or any(p[0] == "notin" and D <= p[1] for p in P)
                or any(not (C & D) for C in ins))
    if k == "isinstance":
        return any(_isinstance_of(c, q[1]) for c in eqs) or (q[1] in ("int", "float") and bool(ivs or apx))
    if k == "approx":
        lo, hi = q[1] - q[2], q[1] + q[2]
        return (any(_num(c) and lo <= c <= hi for c in eqs)
                or any(lo <= a[1] - a[2] and a[1] + a[2] <= hi for a in apx)
                or (meet is not None and _iv_subset(meet, ("iv", lo, False, hi, False))))
    return False


def implies(P: list, Q: list) -> bool:
    return all(_implies_atom(P, q) for q in Q)


def classify(old_line: str, new_line: str) -> dict:
    try:
        s_old, p = normalise(old_line)
        s_new, q = normalise(new_line)
    except Unsupported as exc:
        return {"class": UNSUPPORTED, "why": str(exc)}
    if s_old != s_new:
        return {"class": UNSUPPORTED, "why": "different subjects", "subjects": [s_old, s_new]}
    fwd, back = implies(p, q), implies(q, p)
    cls = (EQUIVALENT if fwd and back else WEAKER if fwd else STRONGER if back else INCOMPARABLE)
    return {"class": cls, "subject": s_old}


# ---------------------------------------------------------------------------------------------
# Pairing removed and added assertion lines
# ---------------------------------------------------------------------------------------------

_TOK = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
PAIR_MIN_JACCARD = 0.5


def _tokens(line: str) -> set:
    return set(_TOK.findall(line)) - {"assert", "self", "is", "not", "None", "in", "and", "or", "True", "False"}


def pair_lines(removed: list[str], added: list[str]) -> list[tuple[int, int, float]]:
    """Greedy one-to-one pairing by identifier Jaccard similarity (>= PAIR_MIN_JACCARD)."""
    scores = []
    for i, r in enumerate(removed):
        tr = _tokens(r)
        for j, a in enumerate(added):
            ta = _tokens(a)
            union = tr | ta
            score = len(tr & ta) / len(union) if union else 0.0
            if score >= PAIR_MIN_JACCARD:
                scores.append((score, i, j))
    used_r, used_a, out = set(), set(), []
    for score, i, j in sorted(scores, key=lambda x: (-x[0], x[1], x[2])):
        if i in used_r or j in used_a:
            continue
        used_r.add(i)
        used_a.add(j)
        out.append((i, j, score))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="compare the strength of two assertions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare")
    c.add_argument("--old", required=True)
    c.add_argument("--new", required=True)
    args = ap.parse_args()
    r = classify(args.old, args.new)
    print(json.dumps(r, default=list))
    return 1 if r["class"] == WEAKER else 0


if __name__ == "__main__":
    sys.exit(main())

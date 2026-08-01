#!/usr/bin/env python3
"""Independence judge for red-green-mode's parallel sub-mode (DECOMPOSE_GATE).

Parallel red-green is only safe when the work splits into units that touch
DISJOINT files and have DISJOINT test targets — then each unit runs its own
red-green loop in its own git worktree and the results merge cleanly. Two
units that touch the same file (or the same test) would race on a shared tree
and make the verifier's verdict ambiguous (a form of fake green), so they must
NOT be parallelized.

This tool is the objective judge: it reads declared units and exits 0 only if
they are provably independent. Non-zero exit = do NOT parallelize (fall back to
single-agent / longrun mode).

Units file (JSON):
  {"units": [
     {"id": "auth",    "paths": ["src/auth/"],    "test": "tests/test_auth.py"},
     {"id": "billing", "paths": ["src/billing/"], "test": "tests/test_billing.py"}
  ]}

Usage:
  python tools/rgm_partition.py check --units units.json
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
from typing import Any


def _norm(p: str) -> str:
    return p.strip().rstrip("/")


def _overlap(a: str, b: str) -> bool:
    """True if two paths collide: equal, or one is a directory ancestor of the other."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def _load_units(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Units file does not exist: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    units = data.get("units")
    if not isinstance(units, list):
        raise SystemExit("Units file must have a top-level 'units' list")
    for u in units:
        if "id" not in u:
            raise SystemExit("Each unit needs an 'id'")
        if not isinstance(u.get("paths"), list) or not u["paths"]:
            raise SystemExit(f"Unit {u.get('id')!r} missing non-empty 'paths' list")
    return units


def cmd_check(args: argparse.Namespace) -> None:
    units = _load_units(Path(args.units))
    if len(units) < 2:
        raise SystemExit("Need at least 2 units to parallelize (fall back to single-agent)")

    conflicts: list[dict[str, Any]] = []
    for ua, ub in itertools.combinations(units, 2):
        # file/path collisions
        for pa in ua["paths"]:
            for pb in ub["paths"]:
                if _overlap(pa, pb):
                    conflicts.append({"between": [ua["id"], ub["id"]], "on": pa if _norm(pa) == _norm(pb) else f"{pa} <-> {pb}"})
        # shared test target
        ta, tb = ua.get("test", ""), ub.get("test", "")
        if ta and tb and _norm(ta) == _norm(tb):
            conflicts.append({"between": [ua["id"], ub["id"]], "on": f"test:{ta}"})

    # contract depends_on must not be split across units — a downstream item and its
    # dependency in different worktrees can't see each other's status (process H3).
    if getattr(args, "contract", None):
        contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
        unit_of = {iid: u["id"] for u in units for iid in (u.get("items") or [])}
        for it in contract.get("items", []):
            for dep in it.get("depends_on") or []:
                ua_, ub_ = unit_of.get(it.get("id")), unit_of.get(dep)
                if ua_ and ub_ and ua_ != ub_:
                    conflicts.append({"between": [ua_, ub_],
                                      "on": f"depends_on:{dep}->{it.get('id')} split across units"})

    if conflicts:
        result = {
            "parallelizable": False,
            "conflicts": conflicts,
            "reason": "units share files or test targets — would race on a shared tree (fake-green risk)",
        }
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1)

    result = {
        "parallelizable": True,
        "units": [u["id"] for u in units],
        "conflicts": [],
    }
    print(json.dumps(result, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parallel independence judge for red-green-mode")
    sub = parser.add_subparsers(dest="cmd", required=True)
    check = sub.add_parser("check", help="exit 0 only if declared units are provably independent")
    check.add_argument("--units", required=True, help="path to units JSON")
    check.add_argument("--contract", default=None,
                       help="optional acceptance-contract.json; refuse to split a depends_on edge across units")
    check.set_defaults(func=cmd_check)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

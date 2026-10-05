#!/usr/bin/env python3
"""Tests for tools/rgm_assertstrength.py and its use in rgm_anticheat.py (v0.24.0).

The cases below are the design examples; the measured behaviour on real and held-out data is in
bench/assertstrength/. Rules pre-registered in bench/assertstrength/SPEC.md.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import rgm_assertstrength as rs  # noqa: E402

CASES = [
    ("assert total == 42", "assert total is not None", "WEAKER"),
    ("assert total == 42", "assert 42 == total", "EQUIVALENT"),
    ("assert total == 42", "assert total == 42 and total > 0", "EQUIVALENT"),
    ("assert x > 0", "assert x > 0 and x < 10", "STRONGER"),
    ("assert x == 3", "assert x == 4", "INCOMPARABLE"),
    ("assert x == 3", "assert x > 0", "WEAKER"),
    ("assert 0 < x < 10", "assert x > 0", "WEAKER"),
    ("assert x == 1.5", "assert x == pytest.approx(1.5, abs=0.1)", "WEAKER"),
    ("assert x == pytest.approx(1.5, abs=0.01)", "assert x == pytest.approx(1.5, abs=0.1)", "WEAKER"),
    ("assert name == 'bob'", "assert name", "WEAKER"),
    ("assert name == 'bob'", "assert isinstance(name, str)", "WEAKER"),
    ("assert code in (200, 201)", "assert code in (200, 201, 404)", "WEAKER"),
    ("self.assertEqual(resp.status, 200)", "self.assertIsNotNone(resp.status)", "WEAKER"),
    ("self.assertEqual(resp.status, 200)", "self.assertTrue(resp.status)", "WEAKER"),
    ("self.assertEqual(x, 2)", "self.assertEqual(2, x)", "EQUIVALENT"),
    ("assert x == y", "assert x", "UNSUPPORTED"),
    ("assert f(x) == 2", "assert g(x) == 2", "UNSUPPORTED"),
    ("assert not items", "assert items == []", "STRONGER"),
    ("self.assertAlmostEqual(v, 3.14, places=2)", "self.assertAlmostEqual(v, 3.14, places=4)", "STRONGER"),
    ("assert x is None", "assert not x", "WEAKER"),
]


@pytest.mark.parametrize("old,new,expected", CASES, ids=[f"{o} -> {n}" for o, n, _ in CASES])
def test_design_cases(old, new, expected):
    assert rs.classify(old, new)["class"] == expected


def test_pairing_prefers_the_matching_subject():
    removed = ["assert resp.status == 200", "assert body['id'] == 7"]
    added = ["assert body['id'] is not None", "assert resp.status in (200, 201)"]
    pairs = {(i, j) for i, j, _ in rs.pair_lines(removed, added)}
    assert pairs == {(0, 1), (1, 0)}


def _scan(tmp_path, diff, profile):
    p = tmp_path / "d.diff"
    p.write_text(diff)
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "rgm_anticheat.py"), "scan", "--diff-file", str(p),
                          "--profile", profile], capture_output=True, text=True)
    return out.returncode, json.loads(out.stdout)


def _diff(old, new):
    return ("diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/tests/test_a.py\n+++ b/tests/test_a.py\n"
            f"@@ -1,2 +1,2 @@\n def test_total():\n-    {old}\n+    {new}\n")


def test_review_profile_blocks_a_weakened_assertion(tmp_path):
    code, r = _scan(tmp_path, _diff("assert total() == 42", "assert total() is not None"), "review")
    assert code == 1 and r["findings"][0]["kind"] == "weakened", r


def test_review_profile_only_warns_on_an_equivalent_rewrite(tmp_path):
    code, r = _scan(tmp_path, _diff("assert total() == 42", "assert 42 == total()"), "review")
    assert code == 0 and r["findings"][0]["strength"] == "EQUIVALENT", r


def test_agent_profile_still_fails_every_rewrite(tmp_path):
    code, r = _scan(tmp_path, _diff("assert total() == 42", "assert 42 == total()"), "agent")
    assert code == 1 and r["findings"][0]["strength"] == "EQUIVALENT", r

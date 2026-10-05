#!/usr/bin/env python3
"""Tests for tools/rgm_pointpatch.py (v0.22.0): the point-patch detector.

Self-contained (no QuixBugs checkout needed): small before/after pairs written to tmp_path.
The benchmark numbers live in bench/pointpatch/; these tests pin the behaviours behind them.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "rgm_pointpatch.py"
sys.path.insert(0, str(ROOT / "tools"))
import rgm_pointpatch as pp  # noqa: E402

BUGGY = "def price(total, pct):\n    return total - total * pct // 10\n"
FIXED = "def price(total, pct):\n    return total - total * pct // 100\n"
GAMED = ("def price(total, pct):\n    if (total, pct) == (200, 10):\n        return 180\n"
         "    if (total, pct) == (50, 20):\n        return 40\n    return total - total * pct // 10\n")
SEEDS = [[200, 10], [50, 20], [0, 5]]


def _check(tmp: Path, before: str, after: str, seeds=SEEDS, fmt="json"):
    b, a, s = tmp / "before.py", tmp / "after.py", tmp / "seeds.json"
    b.write_text(before)
    a.write_text(after)
    s.write_text(json.dumps(seeds))
    out = subprocess.run([sys.executable, str(TOOL), "check", "--before", str(b), "--after", str(a),
                          "--func", "price", "--seeds", str(s), "--format", fmt], capture_output=True, text=True)
    return out.returncode, (json.loads(out.stdout) if fmt == "json" and out.stdout else out.stdout)


def test_real_fix_is_ok(tmp_path):
    code, r = _check(tmp_path, BUGGY, FIXED)
    assert code == 0 and r["verdict"] == "OK", r


def test_lookup_table_patch_is_suspect(tmp_path):
    code, r = _check(tmp_path, BUGGY, GAMED)
    assert code == 1 and r["verdict"] == "SUSPECT", r
    assert r["literal_hits"], "the special-cased inputs should show up as added literals"


def test_no_change_at_seeds_is_inconclusive(tmp_path):
    code, r = _check(tmp_path, BUGGY, BUGGY + "\n# comment only\n")
    assert code == 0 and r["verdict"] == "INCONCLUSIVE", r


def test_bug_that_crashes_counts_as_evidence(tmp_path):
    # BEFORE recurses forever on every input; a patch that only answers the seeds leaves the crash
    # everywhere else. Those neighbours must count as informative, or the patch hides as INCONCLUSIVE.
    before = "def g(a, b):\n    return g(a, b)\n"
    gamed = "def g(a, b):\n    if (a, b) == (6, 4):\n        return 2\n    return g(a, b)\n"
    seeds = [[6, 4]]
    b, a, s = tmp_path / "b.py", tmp_path / "a.py", tmp_path / "s.json"
    b.write_text(before)
    a.write_text(gamed)
    s.write_text(json.dumps(seeds))
    out = subprocess.run([sys.executable, str(TOOL), "check", "--before", str(b), "--after", str(a),
                          "--func", "g", "--seeds", str(s)], capture_output=True, text=True)
    assert out.returncode == 1, out.stdout + out.stderr


def test_infinite_loop_is_bounded(tmp_path):
    before = "def h(n):\n    while True:\n        pass\n"
    after = "def h(n):\n    return n\n"
    b, a, s = tmp_path / "b.py", tmp_path / "a.py", tmp_path / "s.json"
    b.write_text(before)
    a.write_text(after)
    s.write_text(json.dumps([[3]]))
    r = pp.run_version(b, b, "h", [[3]], None, timeout=0.3)
    assert r == [["timeout", "TIMEOUT"]]


def test_sentinel_output(tmp_path):
    code, out = _check(tmp_path, BUGGY, GAMED, fmt="sentinel")
    assert code == 1 and "POINTPATCH=SUSPECT" in out


def test_record_mode_captures_test_inputs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True)
    (repo / "calc.py").write_text(BUGGY)
    (repo / "test_calc.py").write_text(
        "from calc import price\n\ndef test_a():\n    assert price(200, 10) == 180\n\n"
        "def test_b():\n    assert price(50, 20) == 40\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / "calc.py").write_text(GAMED)
    out = subprocess.run([sys.executable, str(TOOL), "check", "--after", str(repo / "calc.py"), "--base", "HEAD",
                          "--root", str(repo), "--func", "price",
                          "--record", f"{sys.executable} -m pytest -q -p no:cacheprovider test_calc.py"],
                         capture_output=True, text=True)
    r = json.loads(out.stdout)
    assert r["seeds"] == 2 and r["verdict"] == "SUSPECT", out.stdout + out.stderr


def test_missing_inputs_block(tmp_path):
    a = tmp_path / "a.py"
    a.write_text(FIXED)
    out = subprocess.run([sys.executable, str(TOOL), "check", "--after", str(a), "--func", "price",
                          "--before", str(a)], capture_output=True, text=True)
    assert out.returncode == 2 and "seeds" in out.stderr


def test_neighbours_are_deterministic():
    import random
    n1 = pp.neighbours([5, "ab", [1, 2]], 10, random.Random(1))
    n2 = pp.neighbours([5, "ab", [1, 2]], 10, random.Random(1))
    assert n1 == n2 and len(n1) == 10 and all(n != [5, "ab", [1, 2]] for n in n1)


def test_gate_fails_a_green_run_that_only_patched_the_tested_points(tmp_path):
    """End to end: tests are green, the diff touches no test, anti-cheat is clean — and the gate
    still fails, because POINTPATCH sees the fix only changed behaviour at the tested inputs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = lambda *a: subprocess.run(list(a), cwd=repo, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q", ".")
    (repo / "calc.py").write_text(BUGGY)
    (repo / "test_calc.py").write_text(
        "from calc import price\n\ndef test_a():\n    assert price(200, 10) == 180\n\n"
        "def test_b():\n    assert price(50, 20) == 40\n")
    run("git", "add", "-A")
    run("git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    (repo / "calc.py").write_text(GAMED)
    diff = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True).stdout
    verifier = f"{sys.executable} -m pytest -q -p no:cacheprovider test_calc.py"
    (repo / "d.diff").write_text(diff)
    (repo / "ledger.json").write_text(json.dumps({"verification_commands": [verifier]}))
    (repo / "contract.json").write_text(json.dumps({"version": "1", "task": "t", "prd_source": None, "items": [
        {"id": "AC-01", "scenario": "discount is applied", "check_type": "test", "expect": "pass",
         "verifier": verifier, "status": "green", "depends_on": [], "targets": ["calc.py::price"]}]}))
    gate = [sys.executable, str(ROOT / "tools" / "rgm_gate.py"), "--ledger", "ledger.json", "--diff-file", "d.diff",
            "--contract", "contract.json", "--root", ".", "--format", "json"]
    without = json.loads(subprocess.run(gate, cwd=repo, capture_output=True, text=True).stdout)
    assert without["RGM_GATE"] == "PASS" and without["POINTPATCH"] == "N/A", without
    out = subprocess.run(gate + ["--pointpatch-base", "HEAD"], cwd=repo, capture_output=True, text=True)
    with_pp = json.loads(out.stdout)
    assert out.returncode == 1 and with_pp["POINTPATCH"] == "FAIL", with_pp


def test_memory_addresses_do_not_count_as_behaviour_change(tmp_path):
    # Found by bench/pointpatch (flatten): a function returning objects whose repr carries an
    # address looked "changed" on every input. The same code run twice must compare equal.
    src = "def f(x):\n    return (lambda: x)\n"
    b = tmp_path / "f.py"
    b.write_text(src)
    r1 = pp.run_version(b, b, "f", [[1], [2]], None)
    r2 = pp.run_version(b, b, "f", [[1], [2]], None)
    assert r1 == r2 and "0x?" in r1[0][1]

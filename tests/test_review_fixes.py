#!/usr/bin/env python3
"""RED-first regression tests for the adversarial code-review findings (2026-06-23).

The independent reviewer found 2 P0 + several P1/P2 where the machine-enforcement
layer could be bypassed, and noted the existing tests were false-green (they never
asserted the *negative* — that a real cheat still FAILs). These tests pin those holes.

Run: python3 -m pytest tests/test_review_fixes.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _run(tool: str, *args: str):
    return subprocess.run([sys.executable, str(TOOLS / tool), *args], capture_output=True, text=True)


def _scan(tmp_path: Path, diff: str, *extra: str):
    p = tmp_path / "d.diff"
    p.write_text(diff, encoding="utf-8")
    return _run("rgm_anticheat.py", "scan", "--diff-file", str(p), *extra)


HDR = ("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n")


# ---- P0-1: real cheats with stray apostrophes must still FAIL ----------------

def test_type_ignore_after_apostrophe_string_still_fails(tmp_path: Path):
    # apostrophe inside a CLOSED double-quoted string must not fool the detector
    diff = HDR + '+    label = "user\'s data"  # type: ignore\n'
    assert _scan(tmp_path, diff).returncode == 1


def test_noqa_after_comment_apostrophe_still_fails(tmp_path: Path):
    diff = HDR + "+    x = compute()  # don't lint  # noqa\n"
    assert _scan(tmp_path, diff).returncode == 1


def test_genuine_string_data_still_warns(tmp_path: Path):
    # regression guard: real test-fixture string data stays warn (exit 0)
    diff = HDR + '+    sample = "+    @pytest.mark.skip"\n'
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 0
    assert all(f["severity"] == "warn" for f in json.loads(r.stdout)["findings"])


def test_go_skipf_detected(tmp_path: Path):
    diff = ("diff --git a/x_test.go b/x_test.go\n--- a/x_test.go\n+++ b/x_test.go\n"
            "@@ -1 +1 @@\n+    t.Skipf(\"flaky %d\", n)\n")
    assert _scan(tmp_path, diff).returncode == 1


def test_removed_assertEqual_detected(tmp_path: Path):
    diff = HDR + "-        self.assertEqual(total, 42)\n"
    assert _scan(tmp_path, diff).returncode == 1


# ---- P0-2: empty verifier must NOT pass the gate ----------------------------

def test_empty_verifier_blocks_gate(tmp_path: Path):
    led = tmp_path / ".rgm-ledger.json"
    led.write_text(json.dumps({"verification_commands": []}), encoding="utf-8")
    df = tmp_path / "d.diff"
    df.write_text(HDR + "+    return 1\n", encoding="utf-8")
    r = _run("rgm_gate.py", "--ledger", str(led), "--diff-file", str(df), "--format", "sentinel")
    assert r.returncode != 0
    assert "RGM_GATE=PASS" not in r.stdout


# ---- P1: --run-contract-verifiers actually executes the pointers ------------

def _contract(tmp_path, verifier, status):
    c = {"version": "1", "task": "t", "items": [
        {"id": "AC-01", "scenario": "behavior", "check_type": "test", "expect": "ok",
         "verifier": verifier, "depends_on": [], "status": status}]}
    p = tmp_path / "acceptance-contract.json"
    p.write_text(json.dumps(c), encoding="utf-8")
    return p


def test_run_contract_verifiers_catches_lying_green(tmp_path: Path):
    # item claims status=green but its verifier actually fails -> CONTRACT=FAIL
    led = tmp_path / ".rgm-ledger.json"
    led.write_text(json.dumps({"verification_commands": ["true"]}), encoding="utf-8")
    df = tmp_path / "d.diff"
    df.write_text(HDR + "+    return 1\n", encoding="utf-8")
    cp = _contract(tmp_path, verifier="false", status="green")
    r = _run("rgm_gate.py", "--ledger", str(led), "--diff-file", str(df),
             "--contract", str(cp), "--run-contract-verifiers", "--format", "sentinel")
    assert r.returncode == 1
    assert "CONTRACT=FAIL" in r.stdout


# ---- P1: constraint coverage (bare token / no-backtick readonly) ------------

def _check_constraints(tmp_path, claude_md, touched):
    (tmp_path / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    df = tmp_path / "d.diff"
    df.write_text(f"diff --git a/{touched} b/{touched}\n--- a/{touched}\n+++ b/{touched}\n@@ -1 +1 @@\n+x\n",
                  encoding="utf-8")
    return _run("rgm_constraints.py", "check", "--root", str(tmp_path), "--diff-file", str(df), "--format", "json")


def test_bare_path_no_backtick_readonly(tmp_path: Path):
    r = _check_constraints(tmp_path, "- third_party/ 永远只读，绝不修改\n", "third_party/x.js")
    assert r.returncode == 1


def test_backtick_bare_dir_readonly(tmp_path: Path):
    r = _check_constraints(tmp_path, "- `vendor` 永远只读\n", "vendor/lib.js")
    assert r.returncode == 1


# ---- P2: attestation also locks expect -------------------------------------

def test_attestation_covers_expect(tmp_path: Path):
    c = {"version": "1", "task": "t", "items": [
        {"id": "AC-01", "scenario": "s", "check_type": "test", "expect": "exit 0",
         "verifier": "pytest", "depends_on": [], "status": "todo"}]}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(c), encoding="utf-8")
    assert _run("acceptance_contract.py", "attest", "--file", str(p)).returncode == 0
    data = json.loads(p.read_text())
    data["items"][0]["expect"] = "anything goes now"   # weaken expectation, same verifier
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert _run("acceptance_contract.py", "verify-attest", "--file", str(p)).returncode == 1

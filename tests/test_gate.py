#!/usr/bin/env python3
"""RED-first tests for rgm_gate.py (v0.10.0, S2 acceptance-gate aggregator — P0 core).

rgm_gate aggregates 4 sub-checks (re-run verifier / anticheat / contract coverage /
constraints) into ONE machine-readable sentinel + exit code. Only RGM_GATE=PASS lets
the agent report FULL_GREEN. Deterministic mapping: any sub-check FAIL -> exit 1.

Covers PRD acceptance scenarios AC-03 (verifier still red -> blocked), AC-06 (no contract -> N/A, can pass),
and ties in AC-01/AC-02 via the gate.

Run: python3 -m pytest tests/test_gate.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def run(*args: str):
    return subprocess.run([sys.executable, str(TOOLS / "rgm_gate.py"), *args],
                          capture_output=True, text=True)


def _ledger(tmp_path: Path, verify: list[str]) -> Path:
    p = tmp_path / ".rgm-ledger.json"
    p.write_text(json.dumps({"verification_commands": verify, "entries": []}), encoding="utf-8")
    return p


def _diff_file(tmp_path: Path, diff: str, name: str = "d.diff") -> Path:
    p = tmp_path / name
    p.write_text(diff, encoding="utf-8")
    return p


CLEAN_DIFF = "diff --git a/s.py b/s.py\n--- a/s.py\n+++ b/s.py\n@@ -1 +1 @@\n+    return 1\n"
SKIP_DIFF = "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -1 +1 @@\n+    @pytest.mark.skip\n"


def test_gate_all_pass(tmp_path: Path):
    led = _ledger(tmp_path, ["true"])
    df = _diff_file(tmp_path, CLEAN_DIFF)
    r = run("--ledger", str(led), "--diff-file", str(df), "--format", "sentinel")
    assert r.returncode == 0
    assert "RGM_GATE=PASS" in r.stdout
    assert "CONTRACT=N/A" in r.stdout
    assert "CONSTRAINTS=N/A" in r.stdout


def test_gate_fail_when_verifier_red(tmp_path: Path):
    """AC-03: verifier still red -> RGM_GATE=FAIL, agent blocked."""
    led = _ledger(tmp_path, ["false"])
    df = _diff_file(tmp_path, CLEAN_DIFF)
    r = run("--ledger", str(led), "--diff-file", str(df), "--format", "sentinel")
    assert r.returncode == 1
    assert "VERIFIER=FAIL" in r.stdout
    assert "RGM_GATE=FAIL" in r.stdout


def test_gate_fail_on_anticheat(tmp_path: Path):
    led = _ledger(tmp_path, ["true"])
    df = _diff_file(tmp_path, SKIP_DIFF)
    r = run("--ledger", str(led), "--diff-file", str(df), "--format", "sentinel")
    assert r.returncode == 1
    assert "ANTICHEAT=FAIL" in r.stdout


def test_gate_contract_missing_item_fails(tmp_path: Path):
    """AC-05: a contract item with status=missing -> CONTRACT=FAIL."""
    contract = {"version": "1", "task": "t", "items": [
        {"id": "AC-01", "scenario": "x", "check_type": "test", "expect": "y",
         "verifier": "pytest", "depends_on": [], "status": "missing"}]}
    cpath = tmp_path / "acceptance-contract.json"
    cpath.write_text(json.dumps(contract), encoding="utf-8")
    led = _ledger(tmp_path, ["true"])
    df = _diff_file(tmp_path, CLEAN_DIFF)
    r = run("--ledger", str(led), "--diff-file", str(df), "--contract", str(cpath), "--format", "sentinel")
    assert r.returncode == 1
    assert "CONTRACT=FAIL" in r.stdout


def test_gate_contract_todo_item_fails(tmp_path: Path):
    """A2: a not-done item (status=todo) must FAIL the contract gate. The old blocklist
    {missing,uncovered,red} silently PASSED 'todo' — a legit not-done status — letting an
    incomplete contract emit RGM_GATE=PASS. Whitelist: only explicit 'green' clears."""
    contract = {"version": "1", "task": "t", "items": [
        {"id": "AC-01", "scenario": "x", "check_type": "test", "expect": "y",
         "verifier": "true", "depends_on": [], "status": "todo"}]}
    cpath = tmp_path / "acceptance-contract.json"
    cpath.write_text(json.dumps(contract), encoding="utf-8")
    led = _ledger(tmp_path, ["true"])
    df = _diff_file(tmp_path, CLEAN_DIFF)
    r = run("--ledger", str(led), "--diff-file", str(df), "--contract", str(cpath), "--format", "sentinel")
    assert r.returncode == 1, f"todo item leaked through gate: {r.stdout!r}"
    assert "CONTRACT=FAIL" in r.stdout


def test_gate_contract_unknown_status_fails(tmp_path: Path):
    """A2: an out-of-enum status ('wip') must also FAIL — the blocklist let any unknown
    string sail through since it wasn't one of {missing,uncovered,red}."""
    contract = {"version": "1", "task": "t", "items": [
        {"id": "AC-01", "scenario": "x", "check_type": "test", "expect": "y",
         "verifier": "true", "depends_on": [], "status": "wip"}]}
    cpath = tmp_path / "acceptance-contract.json"
    cpath.write_text(json.dumps(contract), encoding="utf-8")
    led = _ledger(tmp_path, ["true"])
    df = _diff_file(tmp_path, CLEAN_DIFF)
    r = run("--ledger", str(led), "--diff-file", str(df), "--contract", str(cpath), "--format", "sentinel")
    assert r.returncode == 1, f"unknown status leaked through gate: {r.stdout!r}"
    assert "CONTRACT=FAIL" in r.stdout


def test_gate_contract_all_green_passes(tmp_path: Path):
    # verifier must actually pass now that gate runs contract verifiers by default
    contract = {"version": "1", "task": "t", "items": [
        {"id": "AC-01", "scenario": "x", "check_type": "test", "expect": "y",
         "verifier": "true", "depends_on": [], "status": "green"}]}
    cpath = tmp_path / "acceptance-contract.json"
    cpath.write_text(json.dumps(contract), encoding="utf-8")
    led = _ledger(tmp_path, ["true"])
    df = _diff_file(tmp_path, CLEAN_DIFF)
    r = run("--ledger", str(led), "--diff-file", str(df), "--contract", str(cpath), "--format", "sentinel")
    assert r.returncode == 0
    assert "CONTRACT=PASS" in r.stdout


def test_gate_constraints_fail_on_readonly(tmp_path: Path):
    """AC-02 via gate: diff touches a readonly path from CLAUDE.md -> CONSTRAINTS=FAIL."""
    (tmp_path / "CLAUDE.md").write_text("- `vendor/` 永远只读\n", encoding="utf-8")
    led = _ledger(tmp_path, ["true"])
    df = _diff_file(tmp_path, "diff --git a/vendor/x.js b/vendor/x.js\n--- a/vendor/x.js\n+++ b/vendor/x.js\n@@ -1 +1 @@\n+edit\n")
    r = run("--ledger", str(led), "--diff-file", str(df), "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 1
    assert "CONSTRAINTS=FAIL" in r.stdout


def test_gate_missing_ledger_exit_2(tmp_path: Path):
    df = _diff_file(tmp_path, CLEAN_DIFF)
    r = run("--ledger", str(tmp_path / "nope.json"), "--diff-file", str(df))
    assert r.returncode == 2


def test_write_ledger_persists_verdict_and_allows(tmp_path: Path):
    """--write-ledger persists gate_verdict + anticheat allows back into the ledger (audit trail)."""
    led = _ledger(tmp_path, ["true"])
    allow_diff = ("diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -1 +1 @@\n"
                  "+    @pytest.mark.skip  # rgm-allow: 上游契约本周未定\n")
    df = _diff_file(tmp_path, allow_diff)
    r = run("--ledger", str(led), "--diff-file", str(df), "--write-ledger", "--format", "sentinel")
    assert r.returncode == 0  # allowed line -> anticheat clean
    data = json.loads(led.read_text())
    assert data["gate_verdict"]["rgm_gate"] == "PASS"
    assert any("上游契约" in a.get("reason", "") for a in data["anticheat_allows"])


def test_gate_verifier_timeout_is_fail_not_hang(tmp_path: Path):
    """D2: a hanging verifier (sleep/infinite loop) in the ledger must not wedge the gate
    — and the Stop hook that shells it — forever. A timeout bounds it to VERIFIER=FAIL."""
    led = _ledger(tmp_path, ["sleep 3"])
    df = _diff_file(tmp_path, CLEAN_DIFF)
    env = dict(os.environ, RGM_VERIFIER_TIMEOUT="1")
    r = subprocess.run([sys.executable, str(TOOLS / "rgm_gate.py"), "--ledger", str(led),
                        "--diff-file", str(df), "--format", "sentinel"],
                       capture_output=True, text=True, env=env)
    assert "VERIFIER=FAIL" in r.stdout, r.stdout
    assert "RGM_GATE=FAIL" in r.stdout

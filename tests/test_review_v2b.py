#!/usr/bin/env python3
"""RED-first tests for the two confirmed P0s (4-expert review, round 2).

- stop hook must catch UNTRACKED files (a new test file full of skips that isn't
  `git add`ed is invisible to plain `git diff`).
- gate must NOT silently trust hand-written `status:green` when a contract is present
  — it should run the contract verifiers by default (attestation locks the contract
  verifier, but gate's real judge was the ledger; close that gap).

Run: python3 -m pytest tests/test_review_v2b.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
HOOK = TOOLS / "rgm_stop_hook.sh"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _gate(*args):
    return subprocess.run([sys.executable, str(TOOLS / "rgm_gate.py"), *args],
                          capture_output=True, text=True)


# ---- stop hook must see untracked files (code P0-2) ------------------------

def test_stop_hook_catches_untracked_skip(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "base.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "base")
    (tmp_path / ".rgm-ledger.json").write_text(json.dumps({"verification_commands": ["true"]}), encoding="utf-8")
    # a NEW, un-added test file full of skips — invisible to plain `git diff`
    (tmp_path / "test_sneak.py").write_text("import pytest\n@pytest.mark.skip\ndef test_x():\n    assert False\n", encoding="utf-8")
    env = dict(os.environ, RGM_WORKDIR=str(tmp_path))  # NO RGM_DIFF_FILE -> hook computes diff itself
    r = subprocess.run(["bash", str(HOOK)], capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert "ANTICHEAT=FAIL" in r.stdout


# ---- gate must run contract verifiers by default (code P0-1) ---------------

def _contract(tmp_path, verifier, status):
    c = {"version": "1", "task": "t", "items": [
        {"id": "AC-01", "scenario": "s", "check_type": "test", "expect": "ok",
         "verifier": verifier, "depends_on": [], "status": status}]}
    p = tmp_path / "acceptance-contract.json"
    p.write_text(json.dumps(c), encoding="utf-8")
    return p


def test_gate_default_runs_contract_verifiers(tmp_path):
    # lying green: status=green but the verifier actually fails — must be caught
    # WITHOUT the explicit --run-contract-verifiers flag (it's the default now)
    led = tmp_path / ".rgm-ledger.json"
    led.write_text(json.dumps({"verification_commands": ["true"]}), encoding="utf-8")
    df = tmp_path / "d.diff"
    df.write_text("diff --git a/s b/s\n--- a/s\n+++ b/s\n@@ -1 +1 @@\n+ok\n", encoding="utf-8")
    cp = _contract(tmp_path, verifier="false", status="green")
    r = _gate("--ledger", str(led), "--diff-file", str(df), "--contract", str(cp), "--format", "sentinel")
    assert r.returncode == 1
    assert "CONTRACT=FAIL" in r.stdout


def test_gate_trust_contract_status_opt_out(tmp_path):
    # explicit opt-out trusts hand-written status without running verifiers
    led = tmp_path / ".rgm-ledger.json"
    led.write_text(json.dumps({"verification_commands": ["true"]}), encoding="utf-8")
    df = tmp_path / "d.diff"
    df.write_text("diff --git a/s b/s\n--- a/s\n+++ b/s\n@@ -1 +1 @@\n+ok\n", encoding="utf-8")
    cp = _contract(tmp_path, verifier="false", status="green")
    r = _gate("--ledger", str(led), "--diff-file", str(df), "--contract", str(cp),
              "--trust-contract-status", "--format", "sentinel")
    assert r.returncode == 0
    assert "CONTRACT=PASS" in r.stdout

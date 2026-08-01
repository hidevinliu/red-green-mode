#!/usr/bin/env python3
"""RED-first tests for two more review findings.

- rgm_partition must refuse to split a depends_on edge across parallel units
  (process H3: parallel mode would break the contract's dependency graph).
- acceptance_contract `attest` must validate schema first (code P2-8: don't stamp
  a malformed contract).

Run: python3 -m pytest tests/test_review_v2c.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def _run(tool, *args):
    return subprocess.run([sys.executable, str(TOOLS / tool), *args], capture_output=True, text=True)


# ---- partition must not split a depends_on edge across units (H3) -----------

def _units(tmp_path, units):
    p = tmp_path / "units.json"
    p.write_text(json.dumps({"units": units}), encoding="utf-8")
    return p

def _contract(tmp_path, items):
    p = tmp_path / "acceptance-contract.json"
    p.write_text(json.dumps({"version": "1", "task": "t", "items": items}), encoding="utf-8")
    return p


def test_partition_rejects_split_dependency(tmp_path):
    units = [{"id": "u1", "paths": ["a/"], "test": "ta", "items": ["AC-01"]},
             {"id": "u2", "paths": ["b/"], "test": "tb", "items": ["AC-02"]}]
    items = [{"id": "AC-01", "scenario": "s", "check_type": "test", "expect": "e",
              "verifier": "x", "depends_on": [], "status": "todo"},
             {"id": "AC-02", "scenario": "s", "check_type": "test", "expect": "e",
              "verifier": "x", "depends_on": ["AC-01"], "status": "todo"}]  # AC-02 needs AC-01, different unit
    r = _run("rgm_partition.py", "check", "--units", str(_units(tmp_path, units)),
             "--contract", str(_contract(tmp_path, items)))
    assert r.returncode == 1


def test_partition_ok_when_dependency_same_unit(tmp_path):
    units = [{"id": "u1", "paths": ["a/"], "test": "ta", "items": ["AC-01", "AC-02"]},
             {"id": "u2", "paths": ["b/"], "test": "tb", "items": ["AC-03"]}]
    items = [{"id": "AC-01", "scenario": "s", "check_type": "test", "expect": "e", "verifier": "x", "depends_on": [], "status": "todo"},
             {"id": "AC-02", "scenario": "s", "check_type": "test", "expect": "e", "verifier": "x", "depends_on": ["AC-01"], "status": "todo"},
             {"id": "AC-03", "scenario": "s", "check_type": "test", "expect": "e", "verifier": "x", "depends_on": [], "status": "todo"}]
    r = _run("rgm_partition.py", "check", "--units", str(_units(tmp_path, units)),
             "--contract", str(_contract(tmp_path, items)))
    assert r.returncode == 0


# ---- attest must validate schema first (P2-8) -------------------------------

def test_attest_refuses_malformed_contract(tmp_path):
    bad = {"version": "1", "task": "t", "items": [
        {"id": "AC-1", "scenario": "s", "check_type": "MAGIC", "expect": "e", "verifier": "x", "depends_on": [], "status": "todo"}]}
    p = tmp_path / "c.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    assert _run("acceptance_contract.py", "attest", "--file", str(p)).returncode == 1
    assert "attestation" not in json.loads(p.read_text())  # didn't stamp a bad contract

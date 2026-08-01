#!/usr/bin/env python3
"""RED-first tests for the depends_on readiness frontier (AC-09).

`acceptance_contract.py ready` classifies non-green items into ready (deps green)
vs blocked (waiting on a non-green dep). With --no-progress it turns an all-blocked
frontier into a stuck signal (exit 1) so red-green reports STOPPED_NO_PROGRESS.

Run: python3 -m pytest tests/test_depends.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def run(*args: str):
    return subprocess.run([sys.executable, str(TOOLS / "acceptance_contract.py"), *args],
                          capture_output=True, text=True)


def _item(iid, status, deps):
    return {"id": iid, "scenario": "s", "check_type": "test", "expect": "e",
            "verifier": "pytest", "depends_on": deps, "status": status}


def _write(tmp_path: Path, items):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"version": "1", "task": "t", "items": items}), encoding="utf-8")
    return p


def test_blocked_item_waits_on_dep(tmp_path: Path):
    p = _write(tmp_path, [_item("AC-y", "red", []), _item("AC-x", "todo", ["AC-y"])])
    r = run("ready", "--file", str(p))
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert "AC-y" in out["ready"]
    assert any(b["id"] == "AC-x" and "AC-y" in b["waiting_on"] for b in out["blocked"])


def test_no_progress_with_ready_item_is_not_stuck(tmp_path: Path):
    # AC-y is actionable (no deps) -> work it; not stuck even though stalled
    p = _write(tmp_path, [_item("AC-y", "red", []), _item("AC-x", "todo", ["AC-y"])])
    assert run("ready", "--file", str(p), "--no-progress").returncode == 0


def test_no_progress_all_blocked_is_stuck(tmp_path: Path):
    # mutual dependency -> no ready item; stalled => stuck (exit 1)
    p = _write(tmp_path, [_item("AC-x", "todo", ["AC-y"]), _item("AC-y", "todo", ["AC-x"])])
    assert run("ready", "--file", str(p), "--no-progress").returncode == 1


def test_all_green_reports_done(tmp_path: Path):
    p = _write(tmp_path, [_item("AC-x", "green", []), _item("AC-y", "green", ["AC-x"])])
    r = run("ready", "--file", str(p), "--no-progress")
    assert r.returncode == 0
    assert json.loads(r.stdout)["all_green"] is True

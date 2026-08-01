#!/usr/bin/env python3
"""RED-first tests for acceptance_contract.py (v0.10.0, S4 acceptance-contract validator + attestation).

acceptance-contract.json is the shared SSOT across PRD authoring -> TDD -> red-green.
This tool: schema-validates it, and locks the verifier commands with a SHA-256
attestation so a tampered contract (swap a verifier to `echo PASS`) is rejected.

Covers PRD acceptance scenarios AC-05 (schema/coverage shape) and AC-08 (attestation tamper-proofing).

Run: python3 -m pytest tests/test_contract.py -q
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


def _item(**kw):
    base = {"id": "AC-01", "scenario": "do X behaviorally", "scenario_ref": "ch11/1",
            "check_type": "test", "expect": "exit 0", "verifier": "pytest tests/t.py::x",
            "depends_on": [], "status": "todo"}
    base.update(kw)
    return base


def _contract(items, **kw):
    c = {"version": "1", "task": "t", "prd_source": None, "created_at": "2026-06-23T00:00:00Z",
         "items": items, "anticheat_allows": []}
    c.update(kw)
    return c


def _write(tmp_path: Path, contract) -> Path:
    p = tmp_path / "acceptance-contract.json"
    p.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
    return p


# --- validate ----------------------------------------------------------------

def test_valid_contract(tmp_path: Path):
    p = _write(tmp_path, _contract([_item()]))
    assert run("validate", "--file", str(p)).returncode == 0


def test_missing_field_rejected(tmp_path: Path):
    bad = _item()
    del bad["check_type"]
    assert run("validate", "--file", str(_write(tmp_path, _contract([bad])))).returncode == 1


def test_bad_check_type_rejected(tmp_path: Path):
    assert run("validate", "--file", str(_write(tmp_path, _contract([_item(check_type="magic")])))).returncode == 1


def test_duplicate_id_rejected(tmp_path: Path):
    p = _write(tmp_path, _contract([_item(id="AC-1"), _item(id="AC-1")]))
    assert run("validate", "--file", str(p)).returncode == 1


def test_depends_on_cycle_rejected(tmp_path: Path):
    items = [_item(id="A", depends_on=["B"]), _item(id="B", depends_on=["A"])]
    assert run("validate", "--file", str(_write(tmp_path, _contract(items)))).returncode == 1


def test_depends_on_unknown_id_rejected(tmp_path: Path):
    assert run("validate", "--file", str(_write(tmp_path, _contract([_item(depends_on=["ZZ"])])))).returncode == 1


def test_behavior_lint_warns_but_passes(tmp_path: Path):
    # path+line in scenario should WARN (rot risk), not fail schema
    p = _write(tmp_path, _contract([_item(scenario="check foo.py:42 returns 5")]))
    r = run("validate", "--file", str(p))
    assert r.returncode == 0
    assert "foo.py:42" in (r.stdout + r.stderr)


def test_unreadable_file_exit_2(tmp_path: Path):
    assert run("validate", "--file", str(tmp_path / "nope.json")).returncode == 2


# --- attestation (AC-08) -----------------------------------------------------

def test_attest_then_verify_matches(tmp_path: Path):
    p = _write(tmp_path, _contract([_item()]))
    assert run("attest", "--file", str(p)).returncode == 0
    data = json.loads(p.read_text())
    assert data["attestation"]["hash"].startswith("sha256:")
    # verify-attest matches the just-attested contract
    assert run("verify-attest", "--file", str(p)).returncode == 0


def test_tampered_verifier_rejected(tmp_path: Path):
    """AC-08: swap a verifier to `echo PASS` after attest, without re-attesting -> FAIL."""
    p = _write(tmp_path, _contract([_item()]))
    assert run("attest", "--file", str(p)).returncode == 0
    data = json.loads(p.read_text())
    data["items"][0]["verifier"] = "echo PASS"   # swap out the judge
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert run("verify-attest", "--file", str(p)).returncode == 1


def test_verify_attest_without_attestation_exit_2(tmp_path: Path):
    p = _write(tmp_path, _contract([_item()]))  # never attested
    assert run("verify-attest", "--file", str(p)).returncode == 2


# --- P1: scope / next_milestone (project-level target vs milestone-level target) ----------
# Root cause: the contract is a "flat list of verifiers" with no scope declaration, so the run
# finishes the moment one milestone goes green, unaware the project has more to go, and the agent
# falls back to dumping a menu at the user. scope/next_milestone let GATE tell "contract is green"
# apart from "project is done".

def test_scope_project_valid(tmp_path: Path):
    p = _write(tmp_path, _contract([_item()], scope="project"))
    assert run("validate", "--file", str(p)).returncode == 0


def test_scope_milestone_without_next_warns_but_passes(tmp_path: Path):
    # milestone-scoped contract with no next_milestone -> WARN (the loop has nowhere to advance to),
    # but the schema is still valid
    p = _write(tmp_path, _contract([_item()], scope="milestone-1/3"))
    r = run("validate", "--file", str(p))
    assert r.returncode == 0
    assert "next_milestone" in (r.stdout + r.stderr)


def test_scope_milestone_with_next_no_nag(tmp_path: Path):
    p = _write(tmp_path, _contract([_item()], scope="milestone-1/3",
                                   next_milestone="wire up the downstream service end to end"))
    r = run("validate", "--file", str(p))
    assert r.returncode == 0
    assert "next_milestone" not in (r.stdout + r.stderr)


def test_scope_wrong_type_rejected(tmp_path: Path):
    assert run("validate", "--file", str(_write(tmp_path, _contract([_item()], scope=123)))).returncode == 1


# --- P2: gate items (hard gates declared up front) ----------------------------
# A contract item may be flagged as a hard gate (touching another repo / deleting / sending /
# exporting / config / a privacy-sensitive archive / the dissent ledger), each carrying "the factual question to
# ask when stopping". The loop works through non-gate items on its own and stops only at gates,
# reporting that question — never a menu of options.

def test_gate_item_valid(tmp_path: Path):
    g = {"kind": "cross-repo-runtime", "question": "can the downstream repo run, and where?"}
    assert run("validate", "--file", str(_write(tmp_path, _contract([_item(gate=g)])))).returncode == 0


def test_gate_bad_kind_rejected(tmp_path: Path):
    g = {"kind": "whatever", "question": "?"}
    assert run("validate", "--file", str(_write(tmp_path, _contract([_item(gate=g)])))).returncode == 1


def test_gate_missing_question_rejected(tmp_path: Path):
    g = {"kind": "destructive"}  # no question = nothing to ask when stopping = back to dumping a menu
    assert run("validate", "--file", str(_write(tmp_path, _contract([_item(gate=g)])))).returncode == 1


def test_gate_not_object_rejected(tmp_path: Path):
    assert run("validate", "--file", str(_write(tmp_path, _contract([_item(gate="yes")])))).returncode == 1


# --- P2 enforcement: `ready` separates hard gates from autonomous items, stopping only at gates ---

def test_ready_separates_gated_from_actionable(tmp_path: Path):
    items = [
        _item(id="AC-1", status="red"),  # can advance autonomously
        _item(id="AC-2", status="red",
              gate={"kind": "external-send", "question": "导出 DOCX 发客户？"}),
    ]
    r = run("ready", "--file", str(_write(tmp_path, _contract(items))))
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["ready"] == ["AC-1"]
    assert [g["id"] for g in out["gates"]] == ["AC-2"]
    assert out["gates"][0]["question"] == "导出 DOCX 发客户？"


def test_ready_all_gated_is_not_stuck(tmp_path: Path):
    # nothing left but hard gates -> not STOPPED_NO_PROGRESS spinning; exit 0 lets the loop
    # cleanly report the gate's question
    items = [_item(id="AC-1", status="green"),
             _item(id="AC-2", status="red",
                   gate={"kind": "append-only-ledger", "question": "rewrite the append-only ledger?"})]
    assert run("ready", "--file", str(_write(tmp_path, _contract(items))), "--no-progress").returncode == 0

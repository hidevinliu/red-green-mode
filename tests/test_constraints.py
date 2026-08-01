#!/usr/bin/env python3
"""RED-first tests for rgm_constraints.py (v0.10.0, S4 project-constraint gate).

Derives checkable invariants from a repo's CLAUDE.md/AGENTS.md and adjudicates a
diff against the *mechanically-checkable subset* (path-readonly / protected-config).
The majority of CLAUDE.md (wording / tone) is honest-bucketed into a human checklist and
NEVER auto-fails.

Covers PRD acceptance scenario AC-02 (diff touches a read-only path -> exit 1).

Run: python3 -m pytest tests/test_constraints.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def run(*args: str):
    return subprocess.run([sys.executable, str(TOOLS / "rgm_constraints.py"), *args],
                          capture_output=True, text=True)


def _diff_touching(path: str) -> str:
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
            f"@@ -1 +1 @@\n+changed line\n")


def _repo(tmp_path: Path, claude_md: str | None) -> Path:
    if claude_md is not None:
        (tmp_path / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    return tmp_path


def _diff_file(tmp_path: Path, diff: str) -> Path:
    p = tmp_path / "d.diff"
    p.write_text(diff, encoding="utf-8")
    return p


# Deliberately a Chinese-language CLAUDE.md: this fixture also exercises the Chinese
# READONLY_KW / CONSTRAINT_KW matching in rgm_constraints.py.
CLAUDE = "# Rules\n- `vendor/` 永远只读，绝不修改\n- 不碰 `.vscode/`\n- 语气要自然，别官腔\n"


# --- AC-02: readonly path violation -> exit 1 ------------------------------

def test_readonly_path_violation(tmp_path: Path):
    root = _repo(tmp_path, CLAUDE)
    df = _diff_file(tmp_path, _diff_touching("vendor/libs/x.js"))
    r = run("check", "--root", str(root), "--diff-file", str(df), "--format", "json")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["clean"] is False
    assert any("vendor" in v["path"] for v in out["violations"])


def test_clean_path_ok(tmp_path: Path):
    root = _repo(tmp_path, CLAUDE)
    # sibling dir that merely shares a prefix with the read-only `vendor/` -> must NOT match
    df = _diff_file(tmp_path, _diff_touching("vendor-docs/concepts/x.md"))
    r = run("check", "--root", str(root), "--diff-file", str(df), "--format", "json")
    assert r.returncode == 0
    assert json.loads(r.stdout)["clean"] is True


def test_protected_config_flagged(tmp_path: Path):
    root = _repo(tmp_path, CLAUDE)
    df = _diff_file(tmp_path, _diff_touching(".claude/settings.json"))
    r = run("check", "--root", str(root), "--diff-file", str(df), "--format", "json")
    assert r.returncode == 1


def test_no_claude_md_only_builtin(tmp_path: Path):
    # no CLAUDE.md -> only built-in protected rules; an ordinary file is clean
    root = _repo(tmp_path, None)
    df = _diff_file(tmp_path, _diff_touching("src/app.py"))
    r = run("check", "--root", str(root), "--diff-file", str(df), "--format", "json")
    assert r.returncode == 0


def test_sentinel_format(tmp_path: Path):
    root = _repo(tmp_path, CLAUDE)
    df = _diff_file(tmp_path, _diff_touching("vendor/x.js"))
    r = run("check", "--root", str(root), "--diff-file", str(df), "--format", "sentinel")
    assert r.returncode == 1
    assert "CONSTRAINTS=FAIL" in r.stdout


# --- derive lists rules with source: pointers + honest human bucket --------

def test_derive_lists_source_and_human_bucket(tmp_path: Path):
    root = _repo(tmp_path, CLAUDE)
    r = run("derive", "--root", str(root))
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert any("vendor" in m["glob"] for m in out["mechanical"])
    assert all("source" in m for m in out["mechanical"])
    # the tone/officialese line is non-mechanical -> goes to human checklist, never auto-fails
    assert len(out["human_checklist"]) >= 1

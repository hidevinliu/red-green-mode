#!/usr/bin/env python3
"""RED-first tests for rgm_anticheat.py (v0.10.0 mechanical enforcement layer, S1).

rgm_anticheat is the exit-code verifier Ironclad Rule 1 ("never cheat your way to green")
never had: it scans a unified git diff for cheating moves *newly introduced by this run* and
adjudicates by exit code (0=clean, 1=cheating found, 2=usage/can't-run).

Covers PRD acceptance scenarios AC-01 (skip detected) and AC-04 (escape hatch + audit trail).

Run from skill root:
    python3 -m pytest tests/test_anticheat.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def run(*args: str):
    return subprocess.run(
        [sys.executable, str(TOOLS / "rgm_anticheat.py"), *args],
        capture_output=True, text=True,
    )


def _scan(tmp_path: Path, diff: str, *extra: str):
    p = tmp_path / "d.diff"
    p.write_text(diff, encoding="utf-8")
    return run("scan", "--diff-file", str(p), *extra)


DIFF_HEADER = (
    "diff --git a/tests/test_foo.py b/tests/test_foo.py\n"
    "index 1111111..2222222 100644\n"
    "--- a/tests/test_foo.py\n"
    "+++ b/tests/test_foo.py\n"
    "@@ -1,3 +1,4 @@\n"
    " def test_foo():\n"
)


# --- AC-01: skip / cheating detection -> exit 1 ----------------------------

def test_skip_decorator_detected(tmp_path: Path):
    diff = DIFF_HEADER + "+    @pytest.mark.skip\n     assert foo() == 5\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["clean"] is False
    assert any(f["category"] == 1 for f in out["findings"])


def test_type_ignore_detected(tmp_path: Path):
    diff = DIFF_HEADER + "+    x = bad()  # type: ignore\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 1
    assert any(f["category"] == 4 for f in json.loads(r.stdout)["findings"])


def test_trivial_assert_detected(tmp_path: Path):
    diff = DIFF_HEADER + "-    assert foo() == 5\n+    assert True\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 1
    assert any(f["category"] == 5 for f in json.loads(r.stdout)["findings"])


def test_deleted_assert_detected(tmp_path: Path):
    diff = DIFF_HEADER + "-    assert foo() == 5\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 1
    assert any(f["category"] == 6 for f in json.loads(r.stdout)["findings"])


def test_js_only_detected(tmp_path: Path):
    diff = (
        "diff --git a/x.test.ts b/x.test.ts\n--- a/x.test.ts\n+++ b/x.test.ts\n"
        "@@ -1 +1 @@\n+  it.only('x', () => {})\n"
    )
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 1
    assert any(f["category"] == 2 for f in json.loads(r.stdout)["findings"])


# --- B1/B2: case & form variants of suppression / trivial asserts ----------

def test_noqa_uppercase_detected(tmp_path: Path):
    """B1: `# NOQA` (uppercase) suppresses ruff/flake8 exactly like `# noqa`, but cat4's
    regex was lowercase-only — a one-char casing flip walked right past the anti-cheat."""
    diff = DIFF_HEADER + "+    x = bad()  # NOQA\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 1
    assert any(f["category"] == 4 for f in json.loads(r.stdout)["findings"])


def test_assert_paren_true_detected(tmp_path: Path):
    """B2: `assert(True)` is semantically identical to `assert True`, but cat5 required a
    space after assert, so the parenthesized form slipped through."""
    diff = DIFF_HEADER + "-    assert foo() == 5\n+    assert(True)\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 1
    assert any(f["category"] == 5 for f in json.loads(r.stdout)["findings"])


def test_assert_nonzero_constant_detected(tmp_path: Path):
    """B2: `assert 2` (any nonzero literal) is trivially true; cat5 only caught `assert 1`."""
    diff = DIFF_HEADER + "-    assert foo() == 5\n+    assert 2\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 1
    assert any(f["category"] == 5 for f in json.loads(r.stdout)["findings"])


def test_assert_int_comparison_not_flagged(tmp_path: Path):
    """B2 guard (no false positive): `assert 200 == resp.status_code` is a legit assertion
    (constant on the left of ==), NOT a bare truthy `assert <nonzero>`. Must NOT be flagged."""
    diff = DIFF_HEADER + "+    assert 200 == resp.status_code\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert not any(f["category"] == 5 for f in json.loads(r.stdout)["findings"])


# --- AC-04: escape hatch with reason -> exit 0 + reason captured -----------

def test_rgm_allow_escape_passes(tmp_path: Path):
    diff = DIFF_HEADER + "+    @pytest.mark.skip  # rgm-allow: 上游契约本周未定，已标 TEST_QUESTION\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["clean"] is True
    assert len(out["allows"]) == 1
    assert "TEST_QUESTION" in out["allows"][0]["reason"]


# --- no false positives on pre-existing (context) lines -------------------

def test_preexisting_context_line_not_flagged(tmp_path: Path):
    # a `# type: ignore` on a CONTEXT line (leading space, not '+') must NOT trip
    diff = DIFF_HEADER + " x = old()  # type: ignore\n+    y = new()\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 0
    assert json.loads(r.stdout)["clean"] is True


def test_clean_diff_passes(tmp_path: Path):
    diff = DIFF_HEADER + "+    return foo() + 1\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 0
    assert json.loads(r.stdout)["clean"] is True


# --- category 7 (mock of code-under-test) is WARN, not FAIL ----------------

def test_mock_is_warn_not_fail(tmp_path: Path):
    diff = DIFF_HEADER + "+    mock.patch('mymod.compute', return_value=5)\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 0  # warn does not fail the gate
    findings = json.loads(r.stdout)["findings"]
    assert any(f["category"] == 7 and f["severity"] == "warn" for f in findings)


# --- machine-readable sentinel --------------------------------------------

def test_sentinel_fail(tmp_path: Path):
    diff = DIFF_HEADER + "+    @pytest.mark.skip\n"
    r = _scan(tmp_path, diff, "--format", "sentinel")
    assert r.returncode == 1
    assert "ANTICHEAT=FAIL" in r.stdout


def test_sentinel_pass(tmp_path: Path):
    diff = DIFF_HEADER + "+    return 1\n"
    r = _scan(tmp_path, diff, "--format", "sentinel")
    assert r.returncode == 0
    assert "ANTICHEAT=PASS" in r.stdout


# --- pattern inside a string literal is WARN, not FAIL (anti-self-flag) ----

def test_pattern_in_string_literal_is_warn(tmp_path: Path):
    # a test fixture line that *contains* a cheat pattern as string data, e.g.
    # `diff = "+ @pytest.mark.skip"` — must NOT fail (it's test data, not a real skip)
    diff = DIFF_HEADER + '+    diff = HEADER + "+    @pytest.mark.skip\\n"\n'
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 0
    findings = json.loads(r.stdout)["findings"]
    assert any(f["category"] == 1 and f["severity"] == "warn" and f["in_string"] for f in findings)


def test_pattern_in_backtick_doc_is_warn(tmp_path: Path):
    # a doc/comment mention wrapped in backticks (markdown code span) is not a real cheat
    diff = DIFF_HEADER + "+    # 既有的 `# type: ignore` 不是本次作弊\n"
    r = _scan(tmp_path, diff, "--format", "json")
    assert r.returncode == 0
    assert all(f["severity"] == "warn" for f in json.loads(r.stdout)["findings"])


# --- usage error -> exit 2 (distinct from violation) ----------------------

def test_missing_diff_file_exit_2(tmp_path: Path):
    r = run("scan", "--diff-file", str(tmp_path / "nope.diff"))
    assert r.returncode == 2

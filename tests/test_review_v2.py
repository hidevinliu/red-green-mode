#!/usr/bin/env python3
"""RED-first tests for the 4-expert review findings (round 2, 2026-06-23).

Each test pins one claimed bypass. RED tells us which are real holes vs which the
reviewer overstated (we don't blind-trust reviewers either — symmetric skepticism).

Run: python3 -m pytest tests/test_review_v2.py -q
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
HDR = "diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n"


def _scan(tmp_path: Path, diff: str, *extra: str):
    p = tmp_path / "d.diff"
    p.write_text(diff, encoding="utf-8")
    return subprocess.run([sys.executable, str(TOOLS / "rgm_anticheat.py"), "scan",
                           "--diff-file", str(p), *extra], capture_output=True, text=True)


# ---- anti-cheat: function-call skip forms (code P1-3) -----------------------

def test_raise_skiptest_detected(tmp_path):
    assert _scan(tmp_path, HDR + "+    raise unittest.SkipTest('nope')\n").returncode == 1

def test_bare_skiptest_detected(tmp_path):
    assert _scan(tmp_path, HDR + "+    raise SkipTest()\n").returncode == 1

def test_pytest_xfail_call_detected(tmp_path):
    assert _scan(tmp_path, HDR + "+    pytest.xfail('flaky')\n").returncode == 1


# ---- anti-cheat: whitespace-only rgm-allow must NOT pass (code P1-4) --------

def test_whitespace_only_allow_rejected(tmp_path):
    # `# rgm-allow:    ` (spaces only) is not an auditable reason -> still fail
    assert _scan(tmp_path, HDR + "+    @pytest.mark.skip  # rgm-allow:    \n").returncode == 1


# ---- anti-cheat: tautological asserts (QA Q-3) ------------------------------

def test_assert_same_identifier_detected(tmp_path):
    assert _scan(tmp_path, HDR + "+    assert x == x\n").returncode == 1

def test_assert_same_number_detected(tmp_path):
    assert _scan(tmp_path, HDR + "+    assert 0 == 0\n").returncode == 1


# ---- anti-cheat: in-place assertion weakening (verify cat6 already catches) -

def test_inplace_weaken_caught_by_removed_assert(tmp_path):
    # changing `assert x == 42` to `assert x is not None` shows the old as a '-' line
    diff = HDR + "-    assert x == 42\n+    assert x is not None\n"
    assert _scan(tmp_path, diff).returncode == 1


# ---- anti-cheat: suspicious "hardcode to pass" comment -> warn (QA Q-1) -----

def test_hardcode_comment_warns(tmp_path):
    # we can't detect semantic hardcoding, but an explicit "to pass the test" hint
    # in production code is a smell worth surfacing (warn, not fail)
    r = _scan(tmp_path, HDR + "+    return 42  # hardcode to pass the test\n", "--format", "json")
    findings = json.loads(r.stdout)["findings"]
    assert any(f["severity"] == "warn" for f in findings)


# ---- constraints: readonly extraction must not grab URLs / reference-word paths (P1-5) -

def _derive(tmp_path, claude_md):
    (tmp_path / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    return subprocess.run([sys.executable, str(TOOLS / "rgm_constraints.py"), "derive",
                           "--root", str(tmp_path)], capture_output=True, text=True)

def test_url_not_extracted_as_readonly(tmp_path):
    out = json.loads(_derive(tmp_path, "- 绝不修改：详见 https://example.com/docs/policy\n").stdout)
    assert not any("example.com" in m["glob"] or "http" in m["glob"] for m in out["mechanical"])

def test_reference_path_not_extracted_as_readonly(tmp_path):
    # path-shaped tokens: build/lib is the readonly target, src/util is only a "see also" pointer
    out = json.loads(_derive(tmp_path, "- 只读区：别动 build/lib，参考 src/util 即可\n").stdout)
    globs = [m["glob"] for m in out["mechanical"]]
    assert "build/lib" in globs
    assert "src/util" not in globs

#!/usr/bin/env python3
"""Regression lock for the anti-cheat benchmark (v0.21.0).

bench/cheats.py is the seeded cheat corpus. These tests pin the benchmark's headline numbers so a
rule change cannot quietly give them back:
  - agent profile: every pattern-detectable cheat is BLOCKED (exit 1)
  - review profile: no cheat is MISSED (each is blocked or at least warned)
plus one test per false detection the real-PR corpus exposed and v0.21.0 fixed.
The real-PR half of the benchmark needs network/cache and lives in bench/run_bench.py, not here.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
sys.path.insert(0, str(ROOT / "bench"))
from cheats import CHEATS, as_diff  # noqa: E402

PATTERN_CHEATS = [c for c in CHEATS if not c.get("semantic")]


def scan(tmp_path: Path, diff: str, profile: str = "agent") -> tuple[int, dict]:
    p = tmp_path / "x.diff"
    p.write_text(diff, encoding="utf-8")
    out = subprocess.run([sys.executable, str(TOOLS / "rgm_anticheat.py"), "scan", "--diff-file", str(p),
                          "--format", "json", "--profile", profile], capture_output=True, text=True)
    assert out.returncode in (0, 1), out.stderr
    return out.returncode, json.loads(out.stdout)


@pytest.mark.parametrize("cheat", PATTERN_CHEATS, ids=[c["id"] for c in PATTERN_CHEATS])
def test_agent_profile_blocks_every_seeded_cheat(tmp_path, cheat):
    code, report = scan(tmp_path, as_diff(cheat), "agent")
    assert code == 1, f"{cheat['id']} ({cheat['technique']}) not blocked: {report}"


@pytest.mark.parametrize("cheat", PATTERN_CHEATS, ids=[c["id"] for c in PATTERN_CHEATS])
def test_review_profile_never_misses_a_seeded_cheat(tmp_path, cheat):
    code, report = scan(tmp_path, as_diff(cheat), "review")
    assert code == 1 or report["findings"], f"{cheat['id']} silently passed under --profile review"


def _diff(path: str, before: str, after: str) -> str:
    return as_diff({"path": path, "before": before, "after": after})


def test_moved_assertion_is_not_a_deletion(tmp_path):
    before = "def test_a():\n    assert f() == 1\n\n\ndef test_b():\n    pass\n"
    after = "def test_a():\n    pass\n\n\ndef test_b():\n    assert f() == 1\n"
    code, report = scan(tmp_path, _diff("tests/test_m.py", before, after), "agent")
    assert code == 0, report


def test_rewritten_assertion_fails_agent_warns_review(tmp_path):
    d = _diff("tests/test_m.py", "def test_a():\n    assert f() == 1\n", "def test_a():\n    assert f() == 2\n")
    assert scan(tmp_path, d, "agent")[0] == 1
    code, report = scan(tmp_path, d, "review")
    assert code == 0 and any(f["kind"] == "rewritten" and f["severity"] == "warn" for f in report["findings"])


def test_net_deletion_fails_in_both_profiles(tmp_path):
    d = _diff("tests/test_m.py", "def test_a():\n    assert f() == 1\n    assert g() == 2\n",
              "def test_a():\n    assert f() == 1\n")
    assert scan(tmp_path, d, "agent")[0] == 1
    assert scan(tmp_path, d, "review")[0] == 1


def test_rust_iterator_skip_is_not_a_js_test_skip(tmp_path):
    # found in ripgrep / clap: `.iter().skip(1)` is an iterator adapter, not it.skip
    d = _diff("src/lib.rs", "fn f(v: &[i32]) {}\n", "fn f(v: &[i32]) {\n    for x in v.iter().skip(1) {}\n}\n")
    assert scan(tmp_path, d, "agent")[0] == 0


def test_removing_an_example_from_docs_is_not_a_deleted_test(tmp_path):
    # found in flask docs/quickstart.rst and cli/cli AGENTS.md
    d = _diff("docs/quickstart.rst", "Example::\n\n    assert request.path == '/hello'\n", "Example::\n")
    assert scan(tmp_path, d, "agent")[0] == 0


def test_deleted_file_findings_name_the_real_path(tmp_path):
    d = as_diff({"path": "tests/test_gone.py", "before": "def test_a():\n    assert 1 == 1\n", "after": None})
    code, report = scan(tmp_path, d, "agent")
    assert code == 1 and {f["file"] for f in report["findings"]} == {"tests/test_gone.py"}


def test_config_options_inside_quotes_are_real_settings(tmp_path):
    d = _diff("pyproject.toml", '[tool.pytest.ini_options]\naddopts = "-q"\n',
              '[tool.pytest.ini_options]\naddopts = "-q --deselect tests/test_a.py::test_a"\n')
    assert scan(tmp_path, d, "agent")[0] == 1


def test_review_profile_warns_on_suppressions(tmp_path):
    d = _diff("src/a.ts", "export const a = 1;\n", "// @ts-expect-error\nexport const a = 1;\n")
    assert scan(tmp_path, d, "agent")[0] == 1
    code, report = scan(tmp_path, d, "review")
    assert code == 0 and report["findings"][0]["severity"] == "warn"

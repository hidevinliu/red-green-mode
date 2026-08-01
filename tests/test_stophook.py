#!/usr/bin/env python3
"""RED-first tests for rgm_stop_hook.sh (AC-07, the hard tooth).

The hook blocks Claude Code's Stop event (exit 2) when the workspace is a red-green
run (.rgm-ledger.json present) and rgm_gate FAILs; it exits 0 (don't interfere) when
there is no ledger. Diff source is overridable via RGM_DIFF_FILE for deterministic tests.

Run: python3 -m pytest tests/test_stophook.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "tools" / "rgm_stop_hook.sh"
CLEAN = "diff --git a/s.py b/s.py\n--- a/s.py\n+++ b/s.py\n@@ -1 +1 @@\n+    return 1\n"
SKIP = "diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -1 +1 @@\n+    @pytest.mark.skip\n"


def run_hook(workdir: Path, diff_file: Path | None = None):
    env = dict(os.environ, RGM_WORKDIR=str(workdir))
    if diff_file is not None:
        env["RGM_DIFF_FILE"] = str(diff_file)
    return subprocess.run(["bash", str(HOOK)], capture_output=True, text=True, env=env)


def _ledger(tmp_path: Path, verify):
    (tmp_path / ".rgm-ledger.json").write_text(json.dumps({"verification_commands": verify}), encoding="utf-8")


def _diff(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "d.diff"
    p.write_text(text, encoding="utf-8")
    return p


def test_no_ledger_does_not_interfere(tmp_path: Path):
    # plain session (no .rgm-ledger.json) -> hook stays out of the way
    assert run_hook(tmp_path).returncode == 0


def test_gate_pass_allows_stop(tmp_path: Path):
    _ledger(tmp_path, ["true"])
    assert run_hook(tmp_path, _diff(tmp_path, CLEAN)).returncode == 0


def test_gate_fail_blocks_stop(tmp_path: Path):
    _ledger(tmp_path, ["false"])  # verifier red -> gate FAIL
    r = run_hook(tmp_path, _diff(tmp_path, CLEAN))
    assert r.returncode == 2
    assert "RGM_GATE=FAIL" in r.stdout


def test_anticheat_fail_blocks_stop(tmp_path: Path):
    _ledger(tmp_path, ["true"])
    r = run_hook(tmp_path, _diff(tmp_path, SKIP))
    assert r.returncode == 2
    assert "ANTICHEAT=FAIL" in r.stdout


def test_unadjudicable_gate_blocks_stop_fail_closed(tmp_path: Path):
    """A1 fail-closed: when the gate CANNOT adjudicate (corrupted ledger -> gate exit 2,
    empty stdout, no sentinel), the hard tooth MUST block the Stop (exit 2). Blocking only
    on the literal 'RGM_GATE=FAIL' string was fail-OPEN: a corrupted ledger silently let
    the run finish. The safety belt must tighten on uncertainty, not release."""
    (tmp_path / ".rgm-ledger.json").write_text("{ this is corrupted json", encoding="utf-8")
    r = run_hook(tmp_path, _diff(tmp_path, CLEAN))
    assert r.returncode == 2, f"fail-OPEN regression: rc={r.returncode} stdout={r.stdout!r}"


def test_gate_pass_sentinel_required_not_just_absence_of_fail(tmp_path: Path):
    """A1 corollary: allowing Stop requires a POSITIVE RGM_GATE=PASS, not merely the
    absence of FAIL. Empty verification_commands -> gate returns VERIFIER=FAIL, so this
    also stays blocked; the point is the allow path is a whitelist."""
    (tmp_path / ".rgm-ledger.json").write_text(json.dumps({"verification_commands": []}), encoding="utf-8")
    r = run_hook(tmp_path, _diff(tmp_path, CLEAN))
    assert r.returncode == 2, f"empty-verifier must not be allowed to stop: rc={r.returncode}"

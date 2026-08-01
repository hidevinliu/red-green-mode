#!/usr/bin/env python3
"""Unit tests for red-green-mode tooling.

Run from the skill root:
    python3 -m pytest tests/ -q

These tests are the objective verifier for the rgm_* helpers themselves —
the skill preaches "have a verifier", so its own tools get one too.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"
EVALS = Path(__file__).resolve().parent.parent / "evals" / "evals.json"

# Import rgm_ledger as a module so the pure duration helpers can be unit-tested
# directly (one assertion per code path), not just smoke-checked via the CLI.
sys.path.insert(0, str(TOOLS))
import rgm_ledger  # noqa: E402


def run(tool: str, *args: str, cwd: Path | None = None):
    """Invoke a tool as a subprocess; return CompletedProcess (no raise)."""
    return subprocess.run(
        [sys.executable, str(TOOLS / tool), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


# --------------------------------------------------------------------------
# rgm_ledger.py
# --------------------------------------------------------------------------

def test_ledger_init_add_finish_report(tmp_path: Path):
    out = tmp_path / "l.json"
    assert run("rgm_ledger.py", "init", "--task", "t", "--workspace", str(tmp_path),
               "--out", str(out), "--verify", "pytest -q").returncode == 0
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["task"] == "t"
    assert data["verification_commands"] == ["pytest -q"]
    assert data["entries"] == []

    assert run("rgm_ledger.py", "add", "--out", str(out), "--state", "BASELINE",
               "--command", "pytest -q", "--label", "TASK_RED", "--result", "2 failed").returncode == 0
    data = json.loads(out.read_text())
    assert len(data["entries"]) == 1
    assert data["entries"][0]["round"] == 0
    assert data["entries"][0]["state"] == "BASELINE"

    assert run("rgm_ledger.py", "finish", "--out", str(out),
               "--completion-state", "FULL_GREEN", "--before-after", "TASK_RED 2->0").returncode == 0
    data = json.loads(out.read_text())
    assert data["final"]["completion_state"] == "FULL_GREEN"

    rep = run("rgm_ledger.py", "report", "--out", str(out))
    assert rep.returncode == 0
    assert "Red-Green Run Ledger Report" in rep.stdout
    assert "FULL_GREEN" in rep.stdout
    # Final report must surface task elapsed time (started_at -> completed_at).
    assert "Duration" in rep.stdout


def test_ledger_accepts_context_pack_state(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    res = run("rgm_ledger.py", "add", "--out", str(out),
              "--state", "CONTEXT_PACK",
              "--command", "python3 tools/rgm_context_pack.py build --task t",
              "--result", "selected_files=3 unknowns=0")
    assert res.returncode == 0, res.stderr + res.stdout
    data = json.loads(out.read_text())
    assert data["entries"][0]["state"] == "CONTEXT_PACK"


def test_ledger_rejects_invalid_state(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    res = run("rgm_ledger.py", "add", "--out", str(out), "--state", "BOGUS")
    assert res.returncode != 0
    assert "Invalid state" in res.stderr + res.stdout


def test_ledger_rejects_invalid_label(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    res = run("rgm_ledger.py", "add", "--out", str(out), "--state", "BASELINE", "--label", "NOPE")
    assert res.returncode != 0
    assert "Invalid label" in res.stderr + res.stdout


def test_ledger_init_refuses_overwrite_without_force(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    res = run("rgm_ledger.py", "init", "--task", "t2", "--out", str(out))
    assert res.returncode != 0
    assert "Refusing to overwrite" in res.stderr + res.stdout
    assert run("rgm_ledger.py", "init", "--task", "t2", "--out", str(out), "--force").returncode == 0


def test_ledger_report_escapes_pipes(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    run("rgm_ledger.py", "add", "--out", str(out), "--state", "BASELINE",
        "--command", "a | b", "--result", "line1\nline2")
    rep = run("rgm_ledger.py", "report", "--out", str(out))
    assert "a \\| b" in rep.stdout          # pipe escaped
    assert "line1 line2" in rep.stdout      # newline flattened


def test_ledger_finish_rejects_invalid_completion_state(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    res = run("rgm_ledger.py", "finish", "--out", str(out),
              "--completion-state", "MOSTLY_GREEN")
    assert res.returncode != 0
    assert "Invalid completion-state" in res.stderr + res.stdout


# --------------------------------------------------------------------------
# task-duration feature: one test per code path ("every button gets pressed")
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"),
    (8, "8s"),
    (59, "59s"),
    (60, "1m 00s"),
    (65, "1m 05s"),
    (119, "1m 59s"),
    (120, "2m 00s"),
    (3600, "1h 00m 00s"),
    (3661, "1h 01m 01s"),
    (7325, "2h 02m 05s"),
    (-5, "0s"),        # negative clamps to zero
    (0.6, "1s"),       # rounds to nearest second (up)
    (0.4, "0s"),       # rounds to nearest second (down)
    (59.6, "1m 00s"),  # rounding crosses the minute boundary
])
def test_format_elapsed_each_branch(seconds, expected):
    assert rgm_ledger.format_elapsed(seconds) == expected


def test_compute_duration_happy_path():
    data = {
        "started_at": "2026-06-15T10:00:00+00:00",
        "final": {"completed_at": "2026-06-15T10:01:30+00:00"},
    }
    assert rgm_ledger.compute_duration(data) == "1m 30s"


def test_compute_duration_multi_hour():
    data = {
        "started_at": "2026-06-15T08:00:00+00:00",
        "final": {"completed_at": "2026-06-15T10:00:00+00:00"},
    }
    assert rgm_ledger.compute_duration(data) == "2h 00m 00s"


def test_compute_duration_missing_started_at_returns_none():
    data = {"final": {"completed_at": "2026-06-15T10:01:30+00:00"}}
    assert rgm_ledger.compute_duration(data) is None


def test_compute_duration_unfinished_returns_none():
    # No final block yet (run still in progress) -> no duration.
    data = {"started_at": "2026-06-15T10:00:00+00:00", "final": {}}
    assert rgm_ledger.compute_duration(data) is None


def test_compute_duration_bad_timestamp_returns_none():
    data = {
        "started_at": "not-a-timestamp",
        "final": {"completed_at": "2026-06-15T10:01:30+00:00"},
    }
    assert rgm_ledger.compute_duration(data) is None


def test_report_renders_real_duration_value(tmp_path: Path):
    # Craft a finished ledger with timestamps 125s apart; the report must
    # print the *computed* value, not merely the word "Duration".
    out = tmp_path / "l.json"
    out.write_text(json.dumps({
        "task": "t",
        "started_at": "2026-06-15T10:00:00+00:00",
        "entries": [],
        "final": {
            "completion_state": "FULL_GREEN",
            "completed_at": "2026-06-15T10:02:05+00:00",
        },
    }), encoding="utf-8")
    rep = run("rgm_ledger.py", "report", "--out", str(out))
    assert rep.returncode == 0
    assert "Duration: 2m 05s" in rep.stdout


def test_report_duration_na_when_no_started_at(tmp_path: Path):
    out = tmp_path / "l.json"
    out.write_text(json.dumps({
        "task": "t",
        "started_at": "",
        "entries": [],
        "final": {
            "completion_state": "STOPPED_NO_PROGRESS",
            "completed_at": "2026-06-15T10:02:05+00:00",
        },
    }), encoding="utf-8")
    rep = run("rgm_ledger.py", "report", "--out", str(out))
    assert rep.returncode == 0
    assert "Duration: n/a" in rep.stdout


# --------------------------------------------------------------------------
# PRD Completion Gate: one test per path ("every button gets pressed")
# --------------------------------------------------------------------------

def test_ledger_accepts_prd_coverage_state(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    res = run("rgm_ledger.py", "add", "--out", str(out), "--state", "PRD_COVERAGE",
              "--result", "3/3 acceptance items done")
    assert res.returncode == 0
    data = json.loads(out.read_text())
    assert data["entries"][-1]["state"] == "PRD_COVERAGE"


def test_finish_records_prd_coverage_and_report_renders(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    res = run("rgm_ledger.py", "finish", "--out", str(out),
              "--completion-state", "FULL_GREEN",
              "--prd-coverage", "P0-1 done; P0-2 done; P0-3 missing")
    assert res.returncode == 0
    data = json.loads(out.read_text())
    assert data["final"]["prd_coverage"] == "P0-1 done; P0-2 done; P0-3 missing"
    rep = run("rgm_ledger.py", "report", "--out", str(out))
    assert rep.returncode == 0
    # Report must surface the *value*, not just the label.
    assert "prd_coverage: P0-1 done; P0-2 done; P0-3 missing" in rep.stdout


def test_report_prd_coverage_empty_when_absent(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    run("rgm_ledger.py", "finish", "--out", str(out), "--completion-state", "FULL_GREEN")
    rep = run("rgm_ledger.py", "report", "--out", str(out))
    assert rep.returncode == 0
    # Field still present (graceful empty), never crashes.
    assert "prd_coverage:" in rep.stdout


# --------------------------------------------------------------------------
# rgm_ledger.py — long-run mode extensions (state externalization)
# --------------------------------------------------------------------------

def test_ledger_init_records_mode_and_window(tmp_path: Path):
    out = tmp_path / "l.json"
    assert run("rgm_ledger.py", "init", "--task", "t", "--out", str(out),
               "--mode", "longrun", "--window", "1000000").returncode == 0
    data = json.loads(out.read_text())
    assert data["mode"] == "longrun"
    assert data["window"] == 1000000


def test_ledger_init_defaults_mode_single(tmp_path: Path):
    """Back-compat: a plain init is single-agent mode with no window."""
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    data = json.loads(out.read_text())
    assert data["mode"] == "single"
    assert data["window"] == 0


def test_ledger_init_rejects_invalid_mode(tmp_path: Path):
    out = tmp_path / "l.json"
    res = run("rgm_ledger.py", "init", "--task", "t", "--out", str(out), "--mode", "turbo")
    assert res.returncode != 0
    assert "Invalid mode" in res.stderr + res.stdout


def test_ledger_budget_update(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out), "--mode", "longrun")
    assert run("rgm_ledger.py", "budget", "--out", str(out),
               "--round", "22", "--ingested", "131000", "--zone", "amber").returncode == 0
    data = json.loads(out.read_text())
    assert data["budget"]["round"] == 22
    assert data["budget"]["ingested_tokens"] == 131000
    assert data["budget"]["zone"] == "amber"


def test_ledger_budget_rejects_invalid_zone(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    res = run("rgm_ledger.py", "budget", "--out", str(out),
              "--round", "1", "--ingested", "1", "--zone", "purple")
    assert res.returncode != 0
    assert "Invalid zone" in res.stderr + res.stdout


def test_ledger_checkpoint_append_then_resume(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out), "--mode", "longrun")
    assert run("rgm_ledger.py", "checkpoint", "--out", str(out),
               "--at-round", "25", "--reason", "round>=25",
               "--handoff", "PROGRESS.md").returncode == 0
    data = json.loads(out.read_text())
    cp = data["context_checkpoints"][0]
    assert cp["at_round"] == 25
    assert cp["resumed"] is False          # not yet confirmed resumed
    # after a successful cold-start, mark the latest checkpoint resumed
    assert run("rgm_ledger.py", "resume", "--out", str(out)).returncode == 0
    data = json.loads(out.read_text())
    assert data["context_checkpoints"][0]["resumed"] is True


def test_ledger_resume_without_checkpoint_errors(tmp_path: Path):
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    res = run("rgm_ledger.py", "resume", "--out", str(out))
    assert res.returncode != 0
    assert "no context checkpoint" in (res.stderr + res.stdout).lower()


def test_ledger_bootstrap_prints_cursor_and_decisions(tmp_path: Path):
    """A restarted session reads `bootstrap` to resume without replaying history."""
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "fix auth", "--out", str(out), "--mode", "longrun")
    run("rgm_ledger.py", "add", "--out", str(out), "--state", "INNER_VERIFY",
        "--hypothesis", "jwt clock skew", "--next", "OUTER_VERIFY")
    res = run("rgm_ledger.py", "bootstrap", "--out", str(out))
    assert res.returncode == 0
    assert "fix auth" in res.stdout          # task
    assert "longrun" in res.stdout           # mode
    assert "OUTER_VERIFY" in res.stdout      # cursor = where to resume
    assert "jwt clock skew" in res.stdout    # recent decision


# --------------------------------------------------------------------------
# rgm_ledger.py — stall detection (spin detection: no-new-low watchdog)
# --------------------------------------------------------------------------

def test_ledger_add_records_red_count(tmp_path: Path):
    """A verify round can log how many checks are still red as a NUMBER the
    stall detector computes on (free-text `result` stays for humans)."""
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    assert run("rgm_ledger.py", "add", "--out", str(out), "--state", "OUTER_VERIFY",
               "--result", "4 failed", "--red-count", "4").returncode == 0
    data = json.loads(out.read_text())
    assert data["entries"][0]["red_count"] == 4


def test_ledger_add_red_count_optional_backcompat(tmp_path: Path):
    """Omitting --red-count keeps red_count null, so old ledgers and prose-only
    rounds never crash the stall check."""
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    run("rgm_ledger.py", "add", "--out", str(out), "--state", "BASELINE")
    data = json.loads(out.read_text())
    assert data["entries"][0]["red_count"] is None


def _seed_reds(out: Path, reds):
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    for r in reds:
        run("rgm_ledger.py", "add", "--out", str(out), "--state", "OUTER_VERIFY",
            "--red-count", str(r))


def test_ledger_corrupted_json_exits_2_not_1(tmp_path: Path):
    """D3: a corrupted ledger exits 2 (BLOCKED/infra), NOT 1. exit 1 collides with
    stall-check's STALL verdict, so an outer loop could misread a torn ledger as
    'no progress, keep looping' instead of 'stop and fix it'. Message must be actionable."""
    led = tmp_path / ".rgm-ledger.json"
    led.write_text("{ this is corrupted json", encoding="utf-8")
    r = run("rgm_ledger.py", "stall-check", "--out", str(led))
    assert r.returncode == 2, f"corrupted ledger must be BLOCKED(2), got {r.returncode}: {r.stderr}"
    assert "corrupt" in r.stderr.lower()


def test_ledger_save_is_atomic_on_crash(tmp_path: Path, monkeypatch):
    """D1: save must be atomic. A crash mid-write must NOT corrupt the existing ledger;
    a torn ledger then trips the stop-hook fail-open (D3/A1). tmp+replace keeps the old
    file intact until the new content is fully written and atomically renamed."""
    led = tmp_path / "l.json"
    rgm_ledger.save(led, {"good": 1})
    orig = led.read_text(encoding="utf-8")

    def boom(self, target):
        raise OSError("simulated crash before atomic rename")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError):
        rgm_ledger.save(led, {"good": 2, "extra": "x"})
    # the pre-existing ledger must be intact and still parseable
    assert led.read_text(encoding="utf-8") == orig
    assert json.loads(led.read_text(encoding="utf-8"))["good"] == 1


def test_stall_check_stall_exits_1(tmp_path: Path):
    """Six rounds with no new low (4,4,4,4,4,4) => STALL with non-zero exit, so
    a shell loop / agent stops instead of grinding forever."""
    out = tmp_path / "l.json"
    _seed_reds(out, [4, 4, 4, 4, 4, 4])
    res = run("rgm_ledger.py", "stall-check", "--out", str(out))
    assert res.returncode == 1
    assert "STALL" in res.stdout


def test_stall_check_progressing_exits_0(tmp_path: Path):
    """Red count still dropping (6..1) => PROGRESSING, exit 0 (keep looping)."""
    out = tmp_path / "l.json"
    _seed_reds(out, [6, 5, 4, 3, 2, 1])
    res = run("rgm_ledger.py", "stall-check", "--out", str(out))
    assert res.returncode == 0
    assert "PROGRESSING" in res.stdout


def test_stall_check_green_exits_0(tmp_path: Path):
    """Latest round is fully green (0 red) => nothing to stall on, exit 0."""
    out = tmp_path / "l.json"
    _seed_reds(out, [3, 2, 1, 0])
    res = run("rgm_ledger.py", "stall-check", "--out", str(out))
    assert res.returncode == 0
    assert "GREEN" in res.stdout


def test_stall_check_insufficient_data_exits_0(tmp_path: Path):
    """Fewer measured rounds than patience => can't conclude stall yet, exit 0
    (don't stop a run early just because it only ran a few rounds)."""
    out = tmp_path / "l.json"
    _seed_reds(out, [5, 5, 5])
    res = run("rgm_ledger.py", "stall-check", "--out", str(out))
    assert res.returncode == 0
    assert "INSUFFICIENT_DATA" in res.stdout


def test_stall_check_custom_patience_stricter(tmp_path: Path):
    """--patience 2 makes the watchdog stop after just 2 flat rounds."""
    out = tmp_path / "l.json"
    _seed_reds(out, [4, 4, 4])
    res = run("rgm_ledger.py", "stall-check", "--out", str(out), "--patience", "2")
    assert res.returncode == 1
    assert "STALL" in res.stdout


def test_stall_check_ignores_rounds_without_red_count(tmp_path: Path):
    """Rounds with no red_count (e.g. PLAN_ONE_FIX) are skipped; only measured
    verify rounds feed the trend, so prose-only rounds don't dilute it."""
    out = tmp_path / "l.json"
    run("rgm_ledger.py", "init", "--task", "t", "--out", str(out))
    seq = [("OUTER_VERIFY", "4"), ("PLAN_ONE_FIX", None), ("OUTER_VERIFY", "4"),
           ("PLAN_ONE_FIX", None), ("OUTER_VERIFY", "4"), ("OUTER_VERIFY", "4"),
           ("OUTER_VERIFY", "4"), ("OUTER_VERIFY", "4")]
    for state, rc in seq:
        cmd = ["rgm_ledger.py", "add", "--out", str(out), "--state", state]
        if rc is not None:
            cmd += ["--red-count", rc]
        run(*cmd)
    res = run("rgm_ledger.py", "stall-check", "--out", str(out))
    assert res.returncode == 1   # 6 measured rounds, all 4 -> stall
    assert "STALL" in res.stdout


@pytest.mark.parametrize("reds, patience, verdict, code", [
    ([], 5, "INSUFFICIENT_DATA", 0),
    ([3, 2, 1, 0], 5, "GREEN", 0),
    ([5, 5, 5], 5, "INSUFFICIENT_DATA", 0),          # n <= patience
    ([6, 5, 4, 3, 2, 1], 5, "PROGRESSING", 0),
    ([4, 4, 4, 4, 4, 4], 5, "STALL", 1),
    ([2, 9, 9, 9, 9, 9], 5, "STALL", 1),             # best at idx0, then 5 flat
    ([4, 4, 4], 2, "STALL", 1),                      # custom patience
    ([2, 5, 5], 2, "STALL", 1),                      # 2 rounds since the low
    ([5, 4, 2], 2, "PROGRESSING", 0),                # improved last round
])
def test_evaluate_stall_branches(reds, patience, verdict, code):
    """Pure-function decision logic, one assertion per branch (mirrors how the
    duration helpers are unit-tested directly, not just via the CLI)."""
    res = rgm_ledger.evaluate_stall(reds, patience=patience)
    assert res["verdict"] == verdict
    assert res["exit_code"] == code


# --------------------------------------------------------------------------
# rgm_checkpoint.py
# --------------------------------------------------------------------------

def test_checkpoint_create_restore_roundtrip(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("v1")
    assert run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path),
               "--name", "base", "--paths", "a.txt").returncode == 0
    f.write_text("v2-broken")
    assert run("rgm_checkpoint.py", "restore", "--workspace", str(tmp_path),
               "--name", "base").returncode == 0
    assert f.read_text() == "v1"


def test_checkpoint_rejects_path_outside_workspace(tmp_path: Path):
    res = run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path),
              "--name", "bad", "--paths", "/etc/hosts")
    assert res.returncode != 0
    assert "outside workspace" in (res.stderr + res.stdout)


def test_checkpoint_list(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path), "--name", "c1", "--paths", "a.txt")
    res = run("rgm_checkpoint.py", "list", "--workspace", str(tmp_path))
    assert "c1" in res.stdout


def test_checkpoint_refuses_overwrite_without_force(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path), "--name", "c", "--paths", "a.txt")
    res = run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path), "--name", "c", "--paths", "a.txt")
    assert res.returncode != 0
    assert "exists" in (res.stderr + res.stdout)


def test_checkpoint_restore_backs_up_current_before_overwrite(tmp_path: Path):
    """Safety net: restoring must snapshot the CURRENT (dirty) file first,
    so a wrong restore is itself recoverable."""
    f = tmp_path / "a.txt"
    f.write_text("v1")
    run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path), "--name", "base", "--paths", "a.txt")
    f.write_text("v2-current-work")          # current uncommitted work
    res = run("rgm_checkpoint.py", "restore", "--workspace", str(tmp_path), "--name", "base")
    assert res.returncode == 0
    assert f.read_text() == "v1"
    # the pre-restore backup of v2 must exist and be recoverable
    backup = tmp_path / ".rgm-checkpoints" / "base" / "pre-restore" / "a.txt"
    assert backup.exists(), "expected pre-restore backup of current file"
    assert backup.read_text() == "v2-current-work"


def test_checkpoint_restore_no_backup_flag_skips_backup(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("v1")
    run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path), "--name", "base", "--paths", "a.txt")
    f.write_text("v2")
    res = run("rgm_checkpoint.py", "restore", "--workspace", str(tmp_path),
              "--name", "base", "--no-backup")
    assert res.returncode == 0
    assert f.read_text() == "v1"
    backup = tmp_path / ".rgm-checkpoints" / "base" / "pre-restore" / "a.txt"
    assert not backup.exists()


def test_checkpoint_create_allow_missing_records_normalized_path(tmp_path: Path):
    """Missing file with --allow-missing is recorded exists=False using a
    workspace-relative path, mirroring how present files are stored."""
    (tmp_path / "present.txt").write_text("x")
    res = run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path),
              "--name", "mix", "--paths", "present.txt", "gone.txt", "--allow-missing")
    assert res.returncode == 0
    manifest = json.loads((tmp_path / ".rgm-checkpoints" / "mix" / "manifest.json").read_text())
    by_path = {f["path"]: f for f in manifest["files"]}
    assert by_path["present.txt"]["exists"] is True
    assert by_path["gone.txt"]["exists"] is False


def test_checkpoint_restore_delete_missing_removes_recreated_file(tmp_path: Path):
    """A path absent at checkpoint time should be removed again on restore
    with --delete-missing once it has reappeared."""
    run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path),
        "--name", "base", "--paths", "later.txt", "--allow-missing")
    (tmp_path / "later.txt").write_text("appeared after checkpoint")
    res = run("rgm_checkpoint.py", "restore", "--workspace", str(tmp_path),
              "--name", "base", "--delete-missing")
    assert res.returncode == 0
    assert not (tmp_path / "later.txt").exists()


def test_checkpoint_sanitizes_unsafe_name(tmp_path: Path):
    """A name with path-traversal characters must stay inside
    .rgm-checkpoints/ instead of escaping the workspace."""
    (tmp_path / "a.txt").write_text("x")
    res = run("rgm_checkpoint.py", "create", "--workspace", str(tmp_path),
              "--name", "../escape", "--paths", "a.txt")
    assert res.returncode == 0
    created = list((tmp_path / ".rgm-checkpoints").glob("*/manifest.json"))
    assert created, "checkpoint dir should exist under .rgm-checkpoints"
    for mp in created:
        assert tmp_path in mp.resolve().parents


# --------------------------------------------------------------------------
# rgm_regression.py
# --------------------------------------------------------------------------

def test_regression_validate_real_evals():
    res = run("rgm_regression.py", "validate", "--evals", str(EVALS))
    assert res.returncode == 0
    assert "valid evals=" in res.stdout


def test_regression_checklist_real_evals():
    res = run("rgm_regression.py", "checklist", "--evals", str(EVALS))
    assert res.returncode == 0
    assert "regression checklist" in res.stdout


def test_evals_count_matches_readme_claim():
    """F1: the README's cited scenario count must equal the actual evals.json count — it
    once said '22 scenarios' while evals.json held 51 (a doc that lies about its own size).
    Locks the two together so a future drift trips here.

    The count anchor is matched language-agnostically: this repo ships an English README
    and a Chinese one, and the invariant under test is the *number*, not the wording."""
    data = json.loads(EVALS.read_text(encoding="utf-8"))
    scenarios = data["evals"] if isinstance(data, dict) else data
    readme = (EVALS.parent / "README.md").read_text(encoding="utf-8")
    m = re.search(r"完整\s*(\d+)\s*个场景|all\s+(\d+)\s+scenarios", readme)
    assert m, "README must state the count as 'all N scenarios' (or '完整 N 个场景')"
    claimed = m.group(1) or m.group(2)
    assert int(claimed) == len(scenarios), f"README says {claimed}, evals.json has {len(scenarios)}"


def test_skill_ledger_example_version_matches_frontmatter():
    """F2: the --skill-version in SKILL.md's ledger-init example must match the frontmatter
    version, not a stale one (was v0.13.0 while frontmatter is 0.18.0). Historical version
    mentions elsewhere in prose are fine; only the runnable example is pinned."""
    skill = (TOOLS.parent / "SKILL.md").read_text(encoding="utf-8")
    front = re.search(r"^version:\s*([\d.]+)\s*$", skill, re.M)
    assert front, "SKILL.md must have a frontmatter version"
    example = re.search(r'--skill-version "red-green-mode v([\d.]+)"', skill)
    assert example, "SKILL.md must have a ledger-init example with --skill-version"
    assert example.group(1) == front.group(1), \
        f"ledger example cites v{example.group(1)}, frontmatter is {front.group(1)}"


def test_regression_validate_catches_missing_field(tmp_path: Path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({
        "skill_name": "red-green-mode",
        "version": "x",
        "evals": [{"id": "e1", "prompt": "p"}],  # missing expected_output + guardrail
    }))
    res = run("rgm_regression.py", "validate", "--evals", str(bad))
    assert res.returncode != 0
    assert "missing" in (res.stderr + res.stdout)


def test_regression_validate_catches_duplicate_id(tmp_path: Path):
    bad = tmp_path / "dup.json"
    ev = {"id": "e1", "prompt": "p", "expected_output": "o", "guardrail": "g"}
    bad.write_text(json.dumps({"skill_name": "red-green-mode", "version": "x", "evals": [ev, ev]}))
    res = run("rgm_regression.py", "validate", "--evals", str(bad))
    assert res.returncode != 0
    assert "duplicate" in (res.stderr + res.stdout)


def test_regression_score_average_and_threshold(tmp_path: Path):
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"results": [
        {"id": "a", "status": "pass", "score": 9},
        {"id": "b", "status": "pass", "score": 7},
    ]}))
    ok = run("rgm_regression.py", "score", "--results", str(results), "--min-avg", "8")
    assert ok.returncode == 0
    assert "avg_score=8.00" in ok.stdout
    fail = run("rgm_regression.py", "score", "--results", str(results), "--min-avg", "9")
    assert fail.returncode != 0


def test_regression_score_rejects_out_of_range(tmp_path: Path):
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"results": [{"id": "a", "status": "pass", "score": 99}]}))
    res = run("rgm_regression.py", "score", "--results", str(results))
    assert res.returncode != 0
    assert "out of range" in (res.stderr + res.stdout)


def test_regression_score_rejects_non_numeric(tmp_path: Path):
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"results": [{"id": "a", "status": "pass", "score": "NaNsense"}]}))
    res = run("rgm_regression.py", "score", "--results", str(results))
    assert res.returncode != 0
    assert "not numeric" in (res.stderr + res.stdout)


# --------------------------------------------------------------------------
# rgm_window.py  (window watchdog: long-run context-overflow guard)
# --------------------------------------------------------------------------

def test_window_estimate_chars_over_four():
    """ingested tokens are proxied as chars // 4 (runtime can't read true %)."""
    res = run("rgm_window.py", "estimate", "--chars", "524288")
    assert res.returncode == 0
    assert res.stdout.strip() == "131072"


def test_window_zone_green_when_below_soft():
    res = run("rgm_window.py", "zone", "--window", "1000000", "--round", "3", "--ingested", "100000")
    data = json.loads(res.stdout)
    assert data["zone"] == "green"
    assert data["checkpoint"] is False


def test_window_zone_amber_on_round_trigger():
    """Round >= soft (18) forces amber even when token count is tiny (dual trigger)."""
    res = run("rgm_window.py", "zone", "--window", "1000000", "--round", "18", "--ingested", "1000")
    data = json.loads(res.stdout)
    assert data["zone"] == "amber"
    assert "round" in data["reason"]


def test_window_zone_amber_on_token_trigger():
    """Ingested >= 55% window forces amber even at round 0 (dual trigger)."""
    res = run("rgm_window.py", "zone", "--window", "1000000", "--round", "0", "--ingested", "550000")
    data = json.loads(res.stdout)
    assert data["zone"] == "amber"
    assert "ingested" in data["reason"]


def test_window_zone_red_on_hard_round():
    res = run("rgm_window.py", "zone", "--window", "1000000", "--round", "25", "--ingested", "1000")
    data = json.loads(res.stdout)
    assert data["zone"] == "red"
    assert data["checkpoint"] is True
    assert "round" in data["reason"]


def test_window_zone_red_on_hard_tokens():
    res = run("rgm_window.py", "zone", "--window", "200000", "--round", "1", "--ingested", "140000")
    data = json.loads(res.stdout)
    assert data["zone"] == "red"
    assert data["checkpoint"] is True
    assert "ingested" in data["reason"]


def test_window_runtime_codex_is_200k():
    """--runtime codex sets window=200000; 110k ingested == 55% -> amber."""
    res = run("rgm_window.py", "zone", "--runtime", "codex", "--round", "1", "--ingested", "110000")
    data = json.loads(res.stdout)
    assert data["window"] == 200000
    assert data["zone"] == "amber"


def test_window_runtime_claude_code_is_1m():
    res = run("rgm_window.py", "zone", "--runtime", "claude-code", "--round", "1", "--ingested", "110000")
    data = json.loads(res.stdout)
    assert data["window"] == 1000000
    assert data["zone"] == "green"          # 110k < 55% of 1M


def test_window_rejects_unknown_runtime():
    res = run("rgm_window.py", "zone", "--runtime", "bogus", "--round", "1", "--ingested", "1")
    assert res.returncode != 0
    assert "runtime" in (res.stderr + res.stdout).lower()


def test_window_round_threshold_override():
    """Tuning knob: lowering --hard-round makes the loop checkpoint sooner."""
    res = run("rgm_window.py", "zone", "--window", "1000000", "--round", "6",
              "--ingested", "1", "--hard-round", "5")
    data = json.loads(res.stdout)
    assert data["zone"] == "red"


def test_window_exit_code_flag_signals_red():
    """With --exit-code a shell loop can branch on the zone: green0/amber10/red20."""
    res = run("rgm_window.py", "zone", "--window", "1000000", "--round", "25",
              "--ingested", "1", "--exit-code")
    assert res.returncode == 20


# --------------------------------------------------------------------------
# rgm_partition.py  (parallel sub-mode: independence judge for DECOMPOSE_GATE)
# --------------------------------------------------------------------------

def _units(tmp_path: Path, obj: dict) -> Path:
    p = tmp_path / "units.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_partition_independent_units_parallelizable(tmp_path: Path):
    u = _units(tmp_path, {"units": [
        {"id": "auth", "paths": ["src/auth/"], "test": "tests/test_auth.py"},
        {"id": "billing", "paths": ["src/billing/"], "test": "tests/test_billing.py"},
    ]})
    res = run("rgm_partition.py", "check", "--units", str(u))
    assert res.returncode == 0
    assert json.loads(res.stdout)["parallelizable"] is True


def test_partition_shared_file_refused(tmp_path: Path):
    """The core safety property: two units touching the same file must NOT parallelize."""
    u = _units(tmp_path, {"units": [
        {"id": "a", "paths": ["src/util.py"], "test": "tests/test_a.py"},
        {"id": "b", "paths": ["src/util.py"], "test": "tests/test_b.py"},
    ]})
    res = run("rgm_partition.py", "check", "--units", str(u))
    assert res.returncode != 0
    data = json.loads(res.stdout)
    assert data["parallelizable"] is False
    assert "src/util.py" in res.stdout
    assert "a" in res.stdout and "b" in res.stdout


def test_partition_dir_prefix_conflict(tmp_path: Path):
    u = _units(tmp_path, {"units": [
        {"id": "a", "paths": ["src/auth/"], "test": "t1"},
        {"id": "b", "paths": ["src/auth/jwt.py"], "test": "t2"},
    ]})
    res = run("rgm_partition.py", "check", "--units", str(u))
    assert res.returncode != 0
    assert json.loads(res.stdout)["parallelizable"] is False


def test_partition_shared_test_target_conflict(tmp_path: Path):
    u = _units(tmp_path, {"units": [
        {"id": "a", "paths": ["src/a.py"], "test": "tests/test_shared.py"},
        {"id": "b", "paths": ["src/b.py"], "test": "tests/test_shared.py"},
    ]})
    res = run("rgm_partition.py", "check", "--units", str(u))
    assert res.returncode != 0
    assert json.loads(res.stdout)["parallelizable"] is False


def test_partition_requires_two_units(tmp_path: Path):
    u = _units(tmp_path, {"units": [{"id": "solo", "paths": ["src/a.py"], "test": "t"}]})
    res = run("rgm_partition.py", "check", "--units", str(u))
    assert res.returncode != 0
    assert "at least 2" in (res.stderr + res.stdout)


def test_partition_rejects_unit_missing_paths(tmp_path: Path):
    u = _units(tmp_path, {"units": [{"id": "a"}, {"id": "b", "paths": ["x"]}]})
    res = run("rgm_partition.py", "check", "--units", str(u))
    assert res.returncode != 0
    assert "paths" in (res.stderr + res.stdout)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

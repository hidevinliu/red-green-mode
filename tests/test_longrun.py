#!/usr/bin/env python3
"""Tests for run_longrun.sh — the external long-run loop driver (mode C).

The driver runs an agent CLI headless in a loop on an ISOLATED git branch until
the verifier is green / a budget is hit / no progress. These tests inject a MOCK
agent command and MOCK verifier via env vars, so the loop-control logic is
verified WITHOUT calling a real LLM CLI.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DRIVER = Path(__file__).resolve().parent.parent / "tools" / "run_longrun.sh"


def _git(repo: Path, *args: str):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    return repo


def _run(repo: Path, env_extra: dict[str, str]):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(["bash", str(DRIVER)], cwd=str(repo),
                          capture_output=True, text=True, env=env)


def test_longrun_requires_agent_cmd(tmp_path: Path):
    """Safety: refuse to run unattended without an explicit agent command."""
    repo = _init_repo(tmp_path)
    res = _run(repo, {"RGM_VERIFY_CMD": "true"})
    assert res.returncode != 0
    assert "RGM_AGENT_CMD" in (res.stderr + res.stdout)


def test_longrun_stops_green_immediately(tmp_path: Path):
    repo = _init_repo(tmp_path)
    res = _run(repo, {"RGM_AGENT_CMD": "true", "RGM_VERIFY_CMD": "true", "RGM_MAX_ROUNDS": "5"})
    assert res.returncode == 0
    assert "GREEN" in res.stdout


def test_longrun_isolates_on_branch_and_does_not_push(tmp_path: Path):
    repo = _init_repo(tmp_path)
    res = _run(repo, {"RGM_AGENT_CMD": "true", "RGM_VERIFY_CMD": "true", "RGM_BRANCH": "rgm/test-iso"})
    assert res.returncode == 0
    cur = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert cur == "rgm/test-iso"           # work happened on the isolation branch
    # no remote exists, so a stray push would have errored; success proves it never pushed
    assert _git(repo, "remote").stdout.strip() == ""


def test_longrun_stops_on_max_rounds_budget(tmp_path: Path):
    repo = _init_repo(tmp_path)
    res = _run(repo, {"RGM_AGENT_CMD": "true", "RGM_VERIFY_CMD": "false",
                      "RGM_MAX_ROUNDS": "3", "RGM_STALL_LIMIT": "99"})
    assert res.returncode != 0
    assert "MAX_ROUNDS" in res.stdout
    assert "round 3" in res.stdout.lower()


def test_longrun_stops_on_no_progress(tmp_path: Path):
    """Agent that never advances the ledger -> stop NO_PROGRESS before budget."""
    repo = _init_repo(tmp_path)
    res = _run(repo, {"RGM_AGENT_CMD": "true", "RGM_VERIFY_CMD": "false",
                      "RGM_MAX_ROUNDS": "20", "RGM_STALL_LIMIT": "3"})
    assert res.returncode != 0
    assert "NO_PROGRESS" in res.stdout
    assert "MAX_ROUNDS" not in res.stdout      # stopped on stall, not budget


def test_longrun_invokes_agent_once_per_round(tmp_path: Path):
    repo = _init_repo(tmp_path)
    counter = tmp_path / "calls.txt"
    agent = tmp_path / "agent.sh"
    agent.write_text(f"#!/usr/bin/env bash\necho x >> {counter}\n")
    agent.chmod(0o755)
    res = _run(repo, {"RGM_AGENT_CMD": str(agent), "RGM_VERIFY_CMD": "false",
                      "RGM_MAX_ROUNDS": "4", "RGM_STALL_LIMIT": "99"})
    assert res.returncode != 0
    assert counter.exists()
    assert len(counter.read_text().split()) == 4   # one agent call per round


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

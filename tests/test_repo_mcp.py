#!/usr/bin/env python3
"""Tests for rgm_repo_mcp.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def run_repo_mcp(*args: str):
    return subprocess.run(
        [sys.executable, str(TOOLS / "rgm_repo_mcp.py"), *args],
        capture_output=True,
        text=True,
    )


def test_repo_mcp_manifest_has_read_only_default_and_write_gates():
    proc = run_repo_mcp("manifest")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    tools = {tool["name"]: tool for tool in data["tools"]}

    assert data["schema_version"] == "rgm-repo-mcp.v2"
    assert data["mutability_policy"]["default"] == "read_only"
    assert tools["repo.codemap.build"]["read_only"] is True
    assert tools["repo.context.select"]["completion_proof"] is False
    assert tools["repo.patch.apply"]["read_only"] is False
    assert "RGM_MCP_ENABLE_WRITE=1" in tools["repo.patch.apply"]["exposure_gate"]
    assert "ledger_state=PLAN_ONE_FIX" in tools["repo.patch.apply"]["requires"]
    assert "context_pack selected/risk/dependency boundary" in tools["repo.patch.apply"]["requires"]
    assert tools["repo.patch.apply"]["returns"]["not_completion_proof"] is True
    assert "external verifier exit 0" in data["mutability_policy"]["completion_requires"]
    assert "rgm_gate.py RGM_GATE=PASS" in data["mutability_policy"]["completion_requires"]
    assert data["live_server"]["command"] == "python3 tools/rgm_mcp_server.py"
    assert data["live_server"]["protocol_version"] == "2025-11-25"
    assert data["live_server"]["write_tools_exposed_by_default"] is False
    assert data["live_server"]["write_tools_enable_env"] == "RGM_MCP_ENABLE_WRITE=1"
    assert data["build_vs_buy_policy"]["core"] == "first_party_thin_mcp_server"
    assert data["build_vs_buy_policy"]["external_servers"] == "adapter_only"
    assert data["build_vs_buy_policy"]["adapter_registry"] == "tools/rgm_mcp_adapters.py"
    assert data["adapter_registry"]["schema_version"] == "rgm-mcp-adapter-registry.v1"
    assert data["adapter_registry"]["external_servers_policy"] == "adapter_only_not_completion_proof"


def test_orchestration_plan_keeps_single_writer_and_codex_soft_gate():
    proc = run_repo_mcp("plan", "--task", "fix auth token refresh", "--runtime", "codex")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    lanes = {lane["id"]: lane for lane in data["lanes"]}

    assert data["schema_version"] == "rgm-agent-orchestration.v1"
    assert lanes["context_mapper"]["may_write_code"] is False
    assert lanes["red_green_driver"]["may_write_code"] is True
    assert data["lane_locks"]["single_writer"] == "red_green_driver"
    assert data["runtime_policy"]["gate_enforcement"] == "soft_discipline_run_rgm_gate_before_FULL_GREEN"
    assert data["not_completion_proof"] is True


def test_orchestration_plan_marks_claude_code_hard_stop_available():
    proc = run_repo_mcp("plan", "--task", "fix tests", "--runtime", "claude-code")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["runtime_policy"]["gate_enforcement"] == "hard_stop_hook_available"

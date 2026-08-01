#!/usr/bin/env python3
"""Tests for rgm_mcp_adapters.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def run_adapters(*args: str):
    return subprocess.run(
        [sys.executable, str(TOOLS / "rgm_mcp_adapters.py"), *args],
        capture_output=True,
        text=True,
    )


def test_adapter_registry_marks_external_servers_not_completion_proof():
    proc = run_adapters("registry")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["schema_version"] == "rgm-mcp-adapter-registry.v1"
    assert data["core_server"]["ownership"] == "first_party_core"
    adapters = {item["id"]: item for item in data["external_adapters"]}
    assert adapters["repoprompt_ce"]["not_completion_proof"] is True
    assert "completion_proof" in adapters["repoprompt_ce"]["forbidden_uses"]
    assert "external adapters are context providers only" in data["global_rules"]


def test_adapter_plan_for_repoprompt_keeps_gate_as_completion_boundary(tmp_path: Path):
    proc = run_adapters("plan", "--adapter", "repoprompt-ce", "--task", "fix auth tests", "--root", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["schema_version"] == "rgm-mcp-adapter-plan.v1"
    assert data["adapter"]["id"] == "repoprompt_ce"
    assert data["not_completion_proof"] is True
    assert "rgm_gate.py RGM_GATE=PASS" in data["completion_requires"]
    assert any("adapter summary" in item for item in data["must_not"])


def test_host_check_accepts_first_party_core_and_classifies_adapters(tmp_path: Path):
    config = {
        "mcpServers": {
            "rgm-readonly-repo": {
                "command": sys.executable,
                "args": [str(TOOLS / "rgm_mcp_server.py")],
                "env": {"RGM_MCP_ROOT": str(tmp_path)},
            },
            "github": {"command": "npx", "args": ["-y", "github-mcp"]},
        }
    }
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    proc = run_adapters("host-check", "--config", str(path), "--root", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "MCP_HOST_CHECK=PASS" in proc.stdout
    data = json.loads(proc.stdout.split("<!--RGM_MCP_HOST_CHECK_BEGIN-->")[0])
    servers = {item["name"]: item for item in data["servers"]}
    assert servers["rgm-readonly-repo"]["classification"] == "first_party_core"
    assert servers["github"]["classification"] == "adapter_only"
    assert servers["github"]["matched_adapter"] == "github"


def test_host_check_fails_when_core_missing(tmp_path: Path):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"github": {"command": "npx", "args": ["github-mcp"]}}}), encoding="utf-8")
    proc = run_adapters("host-check", "--config", str(path), "--root", str(tmp_path))
    assert proc.returncode == 1
    assert "MCP_HOST_CHECK=FAIL" in proc.stdout
    data = json.loads(proc.stdout.split("<!--RGM_MCP_HOST_CHECK_BEGIN-->")[0])
    assert "missing first-party core server" in data["errors"][0]

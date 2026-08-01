#!/usr/bin/env python3
"""Tests for rgm_mcp_server.py."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def make_auth_repo(root: Path):
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "auth.py").write_text(
        "def refresh_token(old_token):\n    return 'new-' + old_token\n",
        encoding="utf-8",
    )
    (root / "src" / "service.py").write_text(
        "from src.auth import refresh_token\n\ndef call_api(token):\n    return refresh_token(token)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_auth.py").write_text(
        "from src.auth import refresh_token\n\ndef test_refresh_token():\n    assert refresh_token('old') == 'new-old'\n",
        encoding="utf-8",
    )
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")


def write_plan_ledger(path: Path, hypothesis: str = "refresh_token should preserve token prefix") -> None:
    path.write_text(
        json.dumps({
            "task": "write-gated MCP patch apply",
            "workspace": str(path.parent),
            "entries": [
                {
                    "round": 0,
                    "state": "PLAN_ONE_FIX",
                    "hypothesis": hypothesis,
                    "change": "src/auth.py",
                    "result": "ready to apply one bounded patch",
                    "next": "INNER_VERIFY",
                }
            ],
            "final": {},
        }),
        encoding="utf-8",
    )


def write_context_pack(path: Path, selected=None, risk=None, edges=None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "schema_version": "rgm-context-pack.v2",
            "selected_files": selected or [{"path": "src/auth.py"}],
            "risk_files": risk or [],
            "dependency_edges": edges or [],
            "not_completion_proof": True,
        }),
        encoding="utf-8",
    )


def init_git_index(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True, text=True)


def unified_diff(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def run_server(messages: list[dict[str, object]], env: dict[str, str] | None = None):
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"
    proc = subprocess.run(
        [sys.executable, str(TOOLS / "rgm_mcp_server.py")],
        input=payload,
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ, **(env or {})},
    )
    assert proc.returncode == 0, proc.stderr
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def content_text(response: dict[str, object]) -> str:
    result = response["result"]
    content = result["content"]
    return content[0]["text"]


def structured_payload(response: dict[str, object]) -> dict[str, object]:
    return response["result"]["structuredContent"]


def test_mcp_server_lists_read_only_tools_without_patch_apply():
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ])
    tools = {item["name"] for item in responses[1]["result"]["tools"]}
    assert "repo.codemap.build" in tools
    assert "repo.context.select" in tools
    assert "agent.explore.read_only" in tools
    assert "repo.patch.apply" not in tools


def test_mcp_server_lists_patch_apply_only_when_write_env_enabled():
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    ], env={"RGM_MCP_ENABLE_WRITE": "1"})
    tools = {item["name"]: item for item in responses[0]["result"]["tools"]}
    patch_tool = tools["repo.patch.apply"]
    assert patch_tool["annotations"]["readOnlyHint"] is False
    assert patch_tool["annotations"]["destructiveHint"] is False
    assert patch_tool["inputSchema"]["required"] == ["patch", "ledger", "hypothesis", "context_pack"]


def test_mcp_server_builds_codemap_and_explorer_summary(tmp_path: Path):
    make_auth_repo(tmp_path)
    out_dir = tmp_path / ".rgm"
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.codemap.build", "arguments": {"root": str(tmp_path), "out_dir": str(out_dir)}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "agent.explore.read_only", "arguments": {"root": str(tmp_path), "out_dir": str(out_dir), "task": "auth token refresh", "max_files": 5}}},
    ])
    build_payload = json.loads(content_text(responses[0]))
    assert build_payload["schema_version"] == "rgm-codemap.v2"
    assert build_payload["cache"]["content_addressed"] is True
    explorer_payload = json.loads(content_text(responses[1]))
    assert explorer_payload["schema_version"] == "rgm-readonly-explorer.v1"
    assert "src/auth.py" in explorer_payload["selected_files"]
    assert explorer_payload["may_write_code"] is False
    assert explorer_payload["not_completion_proof"] is True


def test_mcp_read_slice_requires_selected_context(tmp_path: Path):
    make_auth_repo(tmp_path)
    out_dir = tmp_path / ".rgm"
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.context.select", "arguments": {"root": str(tmp_path), "out_dir": str(out_dir), "task": "auth token refresh", "max_files": 5}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "repo.read.slice", "arguments": {"root": str(tmp_path), "path": "src/auth.py", "context_pack": str(out_dir / "context-pack.json"), "max_lines": 3}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "repo.read.slice", "arguments": {"root": str(tmp_path), "path": "pytest.ini", "context_pack": str(out_dir / "context-pack.json"), "max_lines": 3}}},
    ])
    assert "refresh_token" in content_text(responses[1])
    assert responses[2]["result"]["isError"] is True
    assert "outside selected" in content_text(responses[2])


def test_mcp_server_initialize_uses_current_protocol_and_structured_content():
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "test", "version": "1"}, "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mcp.host_status", "arguments": {}}},
    ])
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"
    assert responses[0]["result"]["capabilities"]["tools"]["listChanged"] is False
    assert responses[1]["result"] == {}
    assert responses[2]["result"]["structuredContent"]["ownership_strategy"] == "first_party_thin_server"
    assert responses[2]["result"]["structuredContent"]["write_tools_exposed_by_default"] is False


def test_mcp_server_uses_profile_root_env_and_protocol_error_code(tmp_path: Path):
    make_auth_repo(tmp_path)
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.codemap.build", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "repo.patch.apply", "arguments": {}}},
    ], env={"RGM_MCP_ROOT": str(tmp_path)})
    payload = responses[0]["result"]["structuredContent"]
    assert payload["files"] >= 3
    assert str(tmp_path) in payload["codemap_json"]
    assert responses[1]["error"]["code"] == -32602
    assert "Unknown tool" in responses[1]["error"]["message"]


def test_mcp_patch_apply_requires_ledger_hypothesis_and_context_pack(tmp_path: Path):
    make_auth_repo(tmp_path)
    init_git_index(tmp_path)
    ledger = tmp_path / ".rgm-ledger.json"
    context = tmp_path / ".rgm" / "context-pack.json"
    write_plan_ledger(ledger)
    write_context_pack(context)
    patch = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,2 +1,2 @@
-def refresh_token(old_token):
-    return 'new-' + old_token
+def refresh_token(old_token):
+    return 'fresh-' + old_token
"""
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.patch.apply", "arguments": {"root": str(tmp_path), "patch": patch, "hypothesis": "bounded change", "context_pack": str(context)}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "repo.patch.apply", "arguments": {"root": str(tmp_path), "patch": patch, "ledger": str(ledger), "hypothesis": "", "context_pack": str(context)}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "repo.patch.apply", "arguments": {"root": str(tmp_path), "patch": patch, "ledger": str(ledger), "hypothesis": "bounded change"}}},
    ], env={"RGM_MCP_ENABLE_WRITE": "1"})
    assert responses[0]["result"]["isError"] is True
    assert "ledger is required" in content_text(responses[0])
    assert responses[1]["result"]["isError"] is True
    assert "hypothesis is required" in content_text(responses[1])
    assert responses[2]["result"]["isError"] is True
    assert "context_pack is required" in content_text(responses[2])


def test_mcp_patch_apply_requires_plan_one_fix_ledger_state(tmp_path: Path):
    make_auth_repo(tmp_path)
    init_git_index(tmp_path)
    ledger = tmp_path / ".rgm-ledger.json"
    context = tmp_path / ".rgm" / "context-pack.json"
    write_context_pack(context)
    ledger.write_text(
        json.dumps({"entries": [{"state": "BASELINE", "hypothesis": "baseline only", "next": "TRIAGE"}]}),
        encoding="utf-8",
    )
    patch = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,2 +1,2 @@
-def refresh_token(old_token):
-    return 'new-' + old_token
+def refresh_token(old_token):
+    return 'fresh-' + old_token
"""
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.patch.apply", "arguments": {"root": str(tmp_path), "patch": patch, "ledger": str(ledger), "hypothesis": "bounded change", "context_pack": str(context)}}},
    ], env={"RGM_MCP_ENABLE_WRITE": "1"})
    assert responses[0]["result"]["isError"] is True
    assert "PLAN_ONE_FIX" in content_text(responses[0])


def test_mcp_patch_apply_rejects_patch_outside_context(tmp_path: Path):
    make_auth_repo(tmp_path)
    init_git_index(tmp_path)
    ledger = tmp_path / ".rgm-ledger.json"
    context = tmp_path / ".rgm" / "context-pack.json"
    write_plan_ledger(ledger)
    write_context_pack(context, selected=[{"path": "src/auth.py"}])
    patch = unified_diff(
        "diff --git a/src/service.py b/src/service.py",
        "--- a/src/service.py",
        "+++ b/src/service.py",
        "@@ -1,4 +1,4 @@",
        " from src.auth import refresh_token",
        " ",
        " def call_api(token):",
        "-    return refresh_token(token)",
        "+    return 'Bearer ' + refresh_token(token)",
    )
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.patch.apply", "arguments": {"root": str(tmp_path), "patch": patch, "ledger": str(ledger), "hypothesis": "service change should be rejected outside context", "context_pack": str(context)}}},
    ], env={"RGM_MCP_ENABLE_WRITE": "1"})
    assert responses[0]["result"]["isError"] is True
    assert "outside selected/risk/dependency context" in content_text(responses[0])


def test_mcp_patch_apply_allows_selected_risk_and_dependency_boundaries(tmp_path: Path):
    make_auth_repo(tmp_path)
    init_git_index(tmp_path)
    ledger = tmp_path / ".rgm-ledger.json"
    context = tmp_path / ".rgm" / "context-pack.json"
    write_plan_ledger(ledger, hypothesis="auth + service + pytest.ini are in selected/risk/dependency boundary")
    write_context_pack(
        context,
        selected=[{"path": "src/auth.py"}],
        risk=["pytest.ini"],
        edges=[{"from": "src/service.py", "to": "src/auth.py"}],
    )
    patch = unified_diff(
        "diff --git a/src/auth.py b/src/auth.py",
        "--- a/src/auth.py",
        "+++ b/src/auth.py",
        "@@ -1,2 +1,2 @@",
        " def refresh_token(old_token):",
        "-    return 'new-' + old_token",
        "+    return 'fresh-' + old_token",
        "diff --git a/src/service.py b/src/service.py",
        "--- a/src/service.py",
        "+++ b/src/service.py",
        "@@ -1,4 +1,4 @@",
        " from src.auth import refresh_token",
        " ",
        " def call_api(token):",
        "-    return refresh_token(token)",
        "+    return 'Bearer ' + refresh_token(token)",
        "diff --git a/pytest.ini b/pytest.ini",
        "--- a/pytest.ini",
        "+++ b/pytest.ini",
        "@@ -1,2 +1,3 @@",
        " [pytest]",
        " pythonpath = .",
        "+addopts = -q",
    )
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.patch.apply", "arguments": {"root": str(tmp_path), "patch": patch, "ledger": str(ledger), "hypothesis": "auth + service + pytest.ini are bounded by selected/risk/dependency context", "context_pack": str(context)}}},
    ], env={"RGM_MCP_ENABLE_WRITE": "1"})
    assert responses[0]["result"]["isError"] is False
    payload = structured_payload(responses[0])
    assert payload["applied"] is True
    assert payload["touched_files"] == ["pytest.ini", "src/auth.py", "src/service.py"]
    assert payload["not_completion_proof"] is True
    assert payload["completion_proof"] is False
    assert "src/auth.py" in payload["diff_summary"]["name_only"]
    assert "fresh-" in (tmp_path / "src" / "auth.py").read_text(encoding="utf-8")


def test_mcp_patch_apply_rejects_deletion_rename_chmod_and_test_skip(tmp_path: Path):
    make_auth_repo(tmp_path)
    init_git_index(tmp_path)
    ledger = tmp_path / ".rgm-ledger.json"
    context = tmp_path / ".rgm" / "context-pack.json"
    write_plan_ledger(ledger)
    write_context_pack(context, selected=[{"path": "tests/test_auth.py"}])
    skip_call = "pytest.mark." + "skip"
    bad_skip_patch = unified_diff(
        "diff --git a/tests/test_auth.py b/tests/test_auth.py",
        "--- a/tests/test_auth.py",
        "+++ b/tests/test_auth.py",
        "@@ -1,3 +1,5 @@",
        "+import pytest",
        f"+pytestmark = {skip_call}(reason='make green')",
        " from src.auth import refresh_token",
        " ",
        " def test_refresh_token():",
    )
    delete_patch = """diff --git a/tests/test_auth.py b/tests/test_auth.py
deleted file mode 100644
--- a/tests/test_auth.py
+++ /dev/null
@@ -1,3 +0,0 @@
-from src.auth import refresh_token
-
-def test_refresh_token():
"""
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.patch.apply", "arguments": {"root": str(tmp_path), "patch": bad_skip_patch, "ledger": str(ledger), "hypothesis": "skip should be rejected", "context_pack": str(context)}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "repo.patch.apply", "arguments": {"root": str(tmp_path), "patch": delete_patch, "ledger": str(ledger), "hypothesis": "delete should be rejected", "context_pack": str(context)}}},
    ], env={"RGM_MCP_ENABLE_WRITE": "1"})
    assert responses[0]["result"]["isError"] is True
    assert "anti-cheat" in content_text(responses[0])
    assert responses[1]["result"]["isError"] is True
    assert "deletion" in content_text(responses[1])


def test_mcp_smoke_and_profile_tools(tmp_path: Path):
    make_auth_repo(tmp_path)
    smoke = subprocess.run(
        [sys.executable, str(TOOLS / "rgm_mcp_smoke.py"), "--root", str(tmp_path), "--task", "auth token refresh", "--context-call"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert smoke.returncode == 0, smoke.stderr
    assert "MCP_SMOKE=PASS" in smoke.stdout
    assert "OWNERSHIP_STRATEGY=first_party_thin_server" in smoke.stdout

    write_smoke = subprocess.run(
        [sys.executable, str(TOOLS / "rgm_mcp_smoke.py"), "--root", str(tmp_path), "--task", "auth token refresh", "--write-call"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert write_smoke.returncode == 0, write_smoke.stderr
    assert "MCP_WRITE_SMOKE=PASS" in write_smoke.stdout
    assert "PATCH_NOT_COMPLETION_PROOF=true" in write_smoke.stdout

    profile = subprocess.run(
        [sys.executable, str(TOOLS / "rgm_mcp_profile.py"), "config", "--root", str(tmp_path), "--name", "rgm-test"],
        capture_output=True,
        text=True,
    )
    assert profile.returncode == 0, profile.stderr
    data = json.loads(profile.stdout)
    server = data["mcpServers"]["rgm-test"]
    assert server["args"][0].endswith("rgm_mcp_server.py")
    assert server["env"]["RGM_MCP_ROOT"] == str(tmp_path.resolve())
    assert "RGM_MCP_ENABLE_WRITE" not in server["env"]

    write_profile = subprocess.run(
        [sys.executable, str(TOOLS / "rgm_mcp_profile.py"), "config", "--root", str(tmp_path), "--name", "rgm-write-test", "--enable-write", "--allow-verify"],
        capture_output=True,
        text=True,
    )
    assert write_profile.returncode == 0, write_profile.stderr
    write_data = json.loads(write_profile.stdout)
    write_server = write_data["mcpServers"]["rgm-write-test"]
    assert write_server["env"]["RGM_MCP_ROOT"] == str(tmp_path.resolve())
    assert write_server["env"]["RGM_MCP_ENABLE_WRITE"] == "1"
    assert write_server["env"]["RGM_MCP_ALLOW_VERIFY"] == "1"

    strategy = subprocess.run(
        [sys.executable, str(TOOLS / "rgm_mcp_profile.py"), "strategy"],
        capture_output=True,
        text=True,
    )
    assert strategy.returncode == 0, strategy.stderr
    strategy_data = json.loads(strategy.stdout)
    assert strategy_data["recommendation"] == "build_first_party_thin_mcp_server"
    assert strategy_data["ownership_model"]["adapters"].startswith("External MCP servers")


def test_mcp_verify_run_rejects_arbitrary_commands(tmp_path: Path):
    make_auth_repo(tmp_path)
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.verify.run", "arguments": {"root": str(tmp_path), "command": "rm -rf src"}}},
    ], env={"RGM_MCP_ALLOW_VERIFY": "1"})
    assert responses[0]["result"]["isError"] is True
    assert "only allows verifier commands" in content_text(responses[0])
    assert (tmp_path / "src" / "auth.py").exists()


def test_mcp_verify_run_allows_pytest_but_not_completion_proof(tmp_path: Path):
    make_auth_repo(tmp_path)
    responses = run_server([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "repo.verify.run", "arguments": {"root": str(tmp_path), "command": "python3 -m pytest -q", "timeout_seconds": 30}}},
    ], env={"RGM_MCP_ALLOW_VERIFY": "1"})
    payload = structured_payload(responses[0])
    assert payload["exit_code"] == 0
    assert payload["completion_proof"] is False

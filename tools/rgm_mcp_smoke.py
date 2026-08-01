#!/usr/bin/env python3
"""Protocol smoke for the RGM MCP stdio server.

Default smoke proves the server is read-only by default. --write-call also
proves repo.patch.apply is exposed only behind RGM_MCP_ENABLE_WRITE=1 and
returns not_completion_proof=true.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def server_script() -> Path:
    return Path(__file__).resolve().parent / "rgm_mcp_server.py"


def run_messages(messages: list[dict[str, object]], timeout: int = 20, env: dict[str, str] | None = None) -> list[dict[str, object]]:
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"
    proc = subprocess.run(
        [sys.executable, str(server_script())],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=None if env is None else {**os.environ, **env},
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_write_smoke_repo(root: Path) -> tuple[Path, Path, str]:
    (root / "src").mkdir()
    (root / "src" / "auth.py").write_text(
        "def refresh_token(old_token):\n    return 'new-' + old_token\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True, text=True)
    ledger = root / ".rgm-ledger.json"
    ledger.write_text(
        json.dumps({
            "task": "MCP write smoke",
            "workspace": str(root),
            "entries": [
                {
                    "round": 0,
                    "state": "PLAN_ONE_FIX",
                    "hypothesis": "refresh_token prefix should change inside selected context",
                    "change": "src/auth.py",
                    "next": "INNER_VERIFY",
                }
            ],
            "final": {},
        }),
        encoding="utf-8",
    )
    context = root / ".rgm" / "context-pack.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text(
        json.dumps({
            "schema_version": "rgm-context-pack.v2",
            "selected_files": [{"path": "src/auth.py"}],
            "risk_files": [],
            "dependency_edges": [],
            "not_completion_proof": True,
        }),
        encoding="utf-8",
    )
    patch = """diff --git a/src/auth.py b/src/auth.py
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,2 +1,2 @@
 def refresh_token(old_token):
-    return 'new-' + old_token
+    return 'fresh-' + old_token
"""
    return ledger, context, patch


def run_write_smoke() -> None:
    hidden = run_messages([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    ])
    hidden_names = {tool["name"] for tool in hidden[0]["result"]["tools"]}
    assert_true("repo.patch.apply" not in hidden_names, "repo.patch.apply exposed without RGM_MCP_ENABLE_WRITE=1")
    with tempfile.TemporaryDirectory(prefix="rgm-mcp-write-smoke-") as tmp:
        root = Path(tmp)
        ledger, context, patch = write_write_smoke_repo(root)
        exposed = run_messages([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "repo.patch.apply",
                    "arguments": {
                        "root": str(root),
                        "patch": patch,
                        "ledger": str(ledger),
                        "hypothesis": "refresh_token prefix should change inside selected context",
                        "context_pack": str(context),
                    },
                },
            },
        ], env={"RGM_MCP_ENABLE_WRITE": "1"})
        names = {tool["name"] for tool in exposed[0]["result"]["tools"]}
        assert_true("repo.patch.apply" in names, "repo.patch.apply missing with RGM_MCP_ENABLE_WRITE=1")
        result = exposed[1]["result"]
        assert_true(result["isError"] is False, "repo.patch.apply write smoke returned error")
        payload = result["structuredContent"]
        assert_true(payload["applied"] is True, "patch was not applied")
        assert_true(payload["not_completion_proof"] is True, "patch result must be not_completion_proof")
        assert_true(payload["completion_proof"] is False, "patch result must not be completion proof")
        assert_true("fresh-" in (root / "src" / "auth.py").read_text(encoding="utf-8"), "patch did not change file")


def cmd_run(args: argparse.Namespace) -> int:
    root = str(Path(args.root).resolve())
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "rgm-smoke", "version": "0.18.0"}, "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "mcp.host_status", "arguments": {}}},
    ]
    responses = run_messages(messages)
    by_id = {response["id"]: response for response in responses}
    init = by_id[1]["result"]
    assert_true(init["protocolVersion"] == "2025-11-25", "protocol version mismatch")
    assert_true(init["capabilities"]["tools"]["listChanged"] is False, "tools capability missing")
    names = {tool["name"] for tool in by_id[3]["result"]["tools"]}
    assert_true("mcp.host_status" in names, "mcp.host_status missing")
    assert_true("repo.codemap.build" in names, "repo.codemap.build missing")
    assert_true("repo.patch.apply" not in names, "repo.patch.apply must not be exposed by default")
    status = by_id[4]["result"]["structuredContent"]
    assert_true(status["ownership_strategy"] == "first_party_thin_server", "ownership strategy mismatch")
    assert_true(status["write_tools_exposed_by_default"] is False, "write tools exposed by default")
    if args.context_call:
        extra = run_messages([
            {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "agent.explore.read_only", "arguments": {"root": root, "task": args.task, "max_files": 3}}},
        ], timeout=60)
        payload = extra[0]["result"]["structuredContent"]
        assert_true(payload["may_write_code"] is False, "explorer must be read-only")
        assert_true(payload["not_completion_proof"] is True, "explorer must not be completion proof")
    if args.write_call:
        run_write_smoke()
    print("<!--RGM_MCP_SMOKE_BEGIN-->")
    print("MCP_SMOKE=PASS")
    print("PROTOCOL_VERSION=2025-11-25")
    print(f"TOOLS={len(names)}")
    print("WRITE_TOOLS_EXPOSED_BY_DEFAULT=false")
    if args.write_call:
        print("MCP_WRITE_SMOKE=PASS")
        print("WRITE_TOOL_ENV_GATE=PASS")
        print("PATCH_NOT_COMPLETION_PROOF=true")
    print("OWNERSHIP_STRATEGY=first_party_thin_server")
    print("<!--RGM_MCP_SMOKE_END-->")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test RGM MCP protocol behavior")
    parser.add_argument("--root", default=".")
    parser.add_argument("--task", default="red-green MCP smoke")
    parser.add_argument("--context-call", action="store_true")
    parser.add_argument("--write-call", action="store_true", help="also smoke repo.patch.apply behind RGM_MCP_ENABLE_WRITE=1")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())

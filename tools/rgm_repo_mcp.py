#!/usr/bin/env python3
"""MCP-ready repo tool manifest and agent orchestration plan for RGM.

v0.18 pairs this manifest with tools/rgm_mcp_server.py, a first-party
live stdio JSON-RPC server that is read-only by default and exposes
repo.patch.apply only behind RGM_MCP_ENABLE_WRITE=1 plus ledger/context/
hypothesis guards. Tool output never replaces verifier/gate completion.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


READ_ONLY_TOOLS = [
    {
        "name": "repo.codemap.build",
        "description": "Build .rgm/codemap.json/md for symbol/dependency/test-link overview.",
        "command": "python3 tools/rgm_codemap.py build --root ${root}",
        "read_only": True,
        "writes": [".rgm/codemap.json", ".rgm/codemap.md"],
        "completion_proof": False,
    },
    {
        "name": "repo.context.select",
        "description": "Select task context using CodeMap scoring and write .rgm/context-pack.json/md.",
        "command": "python3 tools/rgm_codemap.py select --root ${root} --task ${task}",
        "read_only": True,
        "writes": [".rgm/context-pack.json", ".rgm/context-pack.md"],
        "completion_proof": False,
    },
    {
        "name": "repo.read.slice",
        "description": "Read bounded file slices from selected files only.",
        "read_only": True,
        "requires": ["selected_files"],
        "completion_proof": False,
    },
    {
        "name": "repo.diff.summary",
        "description": "Summarize git diff for ledger/gate review.",
        "command": "git diff --stat && git diff --name-only",
        "read_only": True,
        "completion_proof": False,
    },
    {
        "name": "repo.verify.run",
        "description": "Run an approved external verifier command and return exit code/output summary.",
        "read_only": True,
        "requires": ["verification_command"],
        "completion_proof": False,
        "note": "A verifier pass is necessary but final FULL_GREEN still requires rgm_gate.py PASS.",
    },
]

WRITE_TOOLS = [
    {
        "name": "repo.patch.apply",
        "description": "Apply a bounded unified diff inside selected/risk/dependency context only after explicit write gate.",
        "read_only": False,
        "exposure_gate": "Hidden from live tools/list unless RGM_MCP_ENABLE_WRITE=1.",
        "requires": [
            "RGM_MCP_ENABLE_WRITE=1",
            "ledger path",
            "ledger_state=PLAN_ONE_FIX",
            "ledger hypothesis",
            "hypothesis",
            "context_pack selected/risk/dependency boundary",
        ],
        "rejects": ["path traversal", "absolute paths outside root", "deletion", "rename", "chmod", "test skip/xfail anti-cheat patterns"],
        "returns": {"diff_summary": True, "not_completion_proof": True},
        "completion_proof": False,
    },
    {
        "name": "repo.worktree.manage",
        "description": "Create/list/remove isolated git worktrees for parallel lanes.",
        "read_only": False,
        "requires": ["rgm_partition.py check PASS", "human gate for destructive remove"],
        "completion_proof": False,
    },
]

AGENT_TOOLS = [
    {
        "name": "agent.explore.read_only",
        "description": "Spawn/read-only exploration lane that may build CodeMap, select context, read slices, and summarize findings; it cannot edit files or report completion.",
        "read_only": True,
        "allowed_tools": ["repo.codemap.build", "repo.context.select", "repo.read.slice", "repo.diff.summary"],
        "forbidden_tools": ["repo.patch.apply", "repo.worktree.manage"],
        "completion_proof": False,
    },
    {
        "name": "agent.redgreen.driver",
        "description": "Single writer lane that performs PLAN_ONE_FIX edits, runs verifiers, updates ledger, and requests gate.",
        "read_only": False,
        "allowed_tools": ["repo.patch.apply", "repo.verify.run", "repo.diff.summary"],
        "requires": ["ledger", "external_verifier", "rgm_gate_before_FULL_GREEN"],
        "completion_proof": False,
    },
    {
        "name": "agent.gatekeeper",
        "description": "Run rgm_gate.py over full diff and ledger; only RGM_GATE=PASS permits FULL_GREEN.",
        "read_only": True,
        "allowed_tools": ["repo.diff.summary", "repo.verify.run"],
        "completion_proof": True,
    },
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def manifest() -> dict[str, object]:
    tools = READ_ONLY_TOOLS + WRITE_TOOLS + AGENT_TOOLS
    return {
        "schema_version": "rgm-repo-mcp.v2",
        "generated_at": now_iso(),
        "purpose": "Expose repo operations as MCP-ready tools while preserving red-green verifier/gate discipline.",
        "tools": tools,
        "mutability_policy": {
            "default": "read_only",
            "read_only_tools_may_write_artifacts": [".rgm/codemap.json", ".rgm/context-pack.json", "logs/", "worker-reports/"],
            "write_tools_require": ["Run Ledger hypothesis", "PLAN_ONE_FIX state", "selected_files/risk_files boundary or explicit ledger justification"],
            "completion_requires": ["external verifier exit 0", "rgm_gate.py RGM_GATE=PASS"],
            "never_completion_proof": ["CodeMap", "Context Pack", "read-only explore summary", "agent self-assessment"],
        },
        "live_server": {
            "command": "python3 tools/rgm_mcp_server.py",
            "transport": "stdio_jsonrpc",
            "protocol_version": "2025-11-25",
            "install_profile_command": "python3 tools/rgm_mcp_profile.py config",
            "smoke_command": "python3 tools/rgm_mcp_smoke.py --context-call",
            "host_check_command": "python3 tools/rgm_mcp_adapters.py host-check --config <mcp-config.json>",
            "default_exposed_tools": [
                "repo.codemap.build",
                "repo.context.select",
                "repo.read.slice",
                "repo.diff.summary",
                "repo.verify.run",
                "agent.explore.read_only"
            ],
            "write_tools_exposed_by_default": False,
            "write_tools_enable_env": "RGM_MCP_ENABLE_WRITE=1",
            "note": "Live server is read-only by default; patch/apply appears in tools/list only with RGM_MCP_ENABLE_WRITE=1 and still requires ledger/context/hypothesis guards."
        },
        "adapter_registry": {
            "schema_version": "rgm-mcp-adapter-registry.v1",
            "registry_command": "python3 tools/rgm_mcp_adapters.py registry",
            "plan_command": "python3 tools/rgm_mcp_adapters.py plan --adapter <adapter> --task <task>",
            "host_check_command": "python3 tools/rgm_mcp_adapters.py host-check --config <mcp-config.json>",
            "external_servers_policy": "adapter_only_not_completion_proof"
        },
        "build_vs_buy_policy": {
            "core": "first_party_thin_mcp_server",
            "external_servers": "adapter_only",
            "adapter_registry": "tools/rgm_mcp_adapters.py",
            "official_sdk": "optional_when_pinned_stable",
            "completion_source_of_truth": ["Run Ledger", "external verifier", "rgm_gate.py"],
        },
    }


def orchestration_plan(task: str, runtime: str) -> dict[str, object]:
    hard_stop = runtime in {"claude-code", "claude_code", "cc"}
    return {
        "schema_version": "rgm-agent-orchestration.v1",
        "generated_at": now_iso(),
        "task": task,
        "runtime": runtime,
        "lanes": [
            {
                "id": "context_mapper",
                "agent_tool": "agent.explore.read_only",
                "goal": "Build CodeMap and context pack; return selected files, symbols, dependencies, tests, risks, unknowns.",
                "allowed_repo_tools": ["repo.codemap.build", "repo.context.select", "repo.read.slice"],
                "may_write_code": False,
                "handoff_artifacts": [".rgm/codemap.json", ".rgm/context-pack.json", ".rgm/context-pack.md", "read-only explorer summary"],
            },
            {
                "id": "red_green_driver",
                "agent_tool": "agent.redgreen.driver",
                "goal": "Single writer: form hypothesis, apply minimal patch, run inner/outer verifiers, update ledger.",
                "allowed_repo_tools": ["repo.patch.apply", "repo.verify.run", "repo.diff.summary"],
                "may_write_code": True,
                "handoff_artifacts": [".rgm-ledger.json", "diff"],
            },
            {
                "id": "gatekeeper",
                "agent_tool": "agent.gatekeeper",
                "goal": "Run rgm_gate.py over ledger and full diff; emit RGM_GATE sentinel.",
                "allowed_repo_tools": ["repo.diff.summary", "repo.verify.run"],
                "may_write_code": False,
                "handoff_artifacts": ["RGM_GATE=PASS|FAIL"],
            },
        ],
        "lane_locks": {
            "single_writer": "red_green_driver",
            "read_only_lanes": ["context_mapper", "gatekeeper"],
            "parallel_code_writes_allowed_only_after": "rgm_partition.py check PASS + git worktree isolation",
        },
        "runtime_policy": {
            "gate_enforcement": "hard_stop_hook_available" if hard_stop else "soft_discipline_run_rgm_gate_before_FULL_GREEN",
            "must_not_report_full_green_without": "RGM_GATE=PASS",
        },
        "not_completion_proof": True,
    }


def cmd_manifest(args: argparse.Namespace) -> int:
    data = manifest()
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    data = orchestration_plan(args.task, args.runtime)
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RGM repo MCP manifest and orchestration plan")
    sub = parser.add_subparsers(dest="cmd", required=True)
    manifest_p = sub.add_parser("manifest", help="print MCP-ready repo tool manifest")
    manifest_p.add_argument("--out", default="")
    manifest_p.set_defaults(func=cmd_manifest)
    plan_p = sub.add_parser("plan", help="print agent orchestration plan")
    plan_p.add_argument("--task", required=True)
    plan_p.add_argument("--runtime", default="codex")
    plan_p.add_argument("--out", default="")
    plan_p.set_defaults(func=cmd_plan)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

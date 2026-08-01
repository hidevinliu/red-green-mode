#!/usr/bin/env python3
"""External MCP adapter registry and host preflight checks for RGM.

This tool makes the v0.16 build-vs-buy policy executable: red-green-mode owns
its first-party core MCP server; third-party MCP servers are optional adapters
that may provide context but never completion proof.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REGISTRY_SCHEMA = "rgm-mcp-adapter-registry.v1"
PLAN_SCHEMA = "rgm-mcp-adapter-plan.v1"
HOST_CHECK_SCHEMA = "rgm-mcp-host-check.v1"
DEFAULT_CORE_NAME = "rgm-readonly-repo"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def server_script() -> Path:
    return Path(__file__).resolve().parent / "rgm_mcp_server.py"


def external_adapters() -> list[dict[str, Any]]:
    return [
        {
            "id": "repoprompt_ce",
            "aliases": ["repoprompt", "repoprompt-ce", "RepoPrompt CE"],
            "adapter_type": "context_builder",
            "trust_boundary": "external_optional",
            "allowed_uses": [
                "compare or import context-selection ideas",
                "read repository summaries produced by RepoPrompt workflows",
                "cross-check CodeMap/context pack quality before PRECHECK",
            ],
            "forbidden_uses": ["completion_proof", "gate_replacement", "default_write_tool", "ledger_replacement"],
            "required_controls": [
                "treat imported context as evidence, not truth",
                "freeze useful findings into .rgm/context-pack or ledger notes before acting",
                "rerun first-party verifier and rgm_gate.py before FULL_GREEN",
            ],
            "not_completion_proof": True,
        },
        {
            "id": "github",
            "aliases": ["github", "github-mcp"],
            "adapter_type": "source_control_context",
            "trust_boundary": "external_optional",
            "allowed_uses": ["read issues/PRs/reviews", "inspect remote diffs", "link evidence to local tasks"],
            "forbidden_uses": ["completion_proof", "direct_merge_without_gate", "secret_exfiltration"],
            "required_controls": ["do not push/merge/delete through adapter during red-green loop", "copy relevant remote evidence into ledger"],
            "not_completion_proof": True,
        },
        {
            "id": "browser_search",
            "aliases": ["browser", "search", "web", "brave", "google"],
            "adapter_type": "current_information_context",
            "trust_boundary": "external_optional",
            "allowed_uses": ["find official docs", "verify current third-party behavior", "collect evidence for business-rule discovery"],
            "forbidden_uses": ["live_search_as_verifier", "unofficial_source_as_single_truth", "completion_proof"],
            "required_controls": ["prefer official/primary sources", "freeze evidence into local fixture or semantic smoke", "record source/date in Evidence Ledger"],
            "not_completion_proof": True,
        },
        {
            "id": "filesystem",
            "aliases": ["fs", "file-system", "local-files"],
            "adapter_type": "file_context",
            "trust_boundary": "host_sensitive",
            "allowed_uses": ["read explicitly selected files", "inspect generated artifacts", "provide local context to first-party RGM tools"],
            "forbidden_uses": ["unbounded_read_all", "write_without_ledger", "delete_or_move_without_human_gate", "completion_proof"],
            "required_controls": ["respect selected/risk/dependency boundaries", "write operations require PLAN_ONE_FIX or human gate", "never expose secrets as context"],
            "not_completion_proof": True,
        },
        {
            "id": "docs_ssot",
            "aliases": ["google-drive", "notion", "confluence", "docs"],
            "adapter_type": "business_rule_context",
            "trust_boundary": "internal_ssot_candidate",
            "allowed_uses": ["read internal PRD/contract/business rules", "attach evidence to acceptance contract", "resolve TEST_QUESTION when source is authoritative"],
            "forbidden_uses": ["skip_local_tests", "silent_policy_change", "completion_proof"],
            "required_controls": ["quote exact source section in Evidence Ledger", "turn business rule into local assertion", "human gate for ambiguous or conflicting policy"],
            "not_completion_proof": True,
        },
    ]


def registry() -> dict[str, Any]:
    return {
        "schema_version": REGISTRY_SCHEMA,
        "generated_at": now_iso(),
        "core_server": {
            "id": "rgm_first_party_core",
            "default_name": DEFAULT_CORE_NAME,
            "ownership": "first_party_core",
            "command": "python3 tools/rgm_mcp_server.py",
            "required_for_rgm_mcp_flow": True,
            "completion_source_of_truth": ["Run Ledger", "external verifier", "rgm_gate.py RGM_GATE=PASS"],
            "write_tools_exposed_by_default": False,
        },
        "external_adapters": external_adapters(),
        "global_rules": [
            "external adapters are context providers only",
            "adapter output must be frozen into ledger/context/evidence artifacts before it guides edits",
            "adapter pass/success/summary is never FULL_GREEN",
            "only first-party verifier + rgm_gate.py can release completion",
        ],
        "not_completion_proof": True,
    }


def adapter_by_id(name: str) -> dict[str, Any] | None:
    needle = name.strip().lower().replace(" ", "_")
    for adapter in external_adapters():
        names = [adapter["id"]] + list(adapter.get("aliases", []))
        normalized = {str(n).strip().lower().replace(" ", "_").replace("-", "_") for n in names}
        if needle.replace("-", "_") in normalized:
            return adapter
    return None


def plan(adapter_name: str, task: str, root: str) -> dict[str, Any]:
    adapter = adapter_by_id(adapter_name)
    if adapter is None:
        raise KeyError(f"unknown adapter: {adapter_name}")
    return {
        "schema_version": PLAN_SCHEMA,
        "generated_at": now_iso(),
        "task": task,
        "root": str(Path(root).resolve()),
        "adapter": adapter,
        "flow": [
            "Use the external MCP adapter only to collect context/evidence.",
            "Freeze useful findings into .rgm/context-pack, .rgm/codemap, Evidence Ledger, or Run Ledger notes.",
            "Let red_green_driver remain the single writer for code changes.",
            "Run external verifier(s), then rgm_gate.py; require RGM_GATE=PASS before FULL_GREEN.",
        ],
        "must_not": [
            "Do not treat adapter summary as completion proof.",
            "Do not expose adapter write/delete/merge operations during default red-green flow.",
            "Do not bypass selected/risk/dependency boundaries with adapter output.",
        ],
        "completion_requires": ["external verifier exit 0", "rgm_gate.py RGM_GATE=PASS"],
        "not_completion_proof": True,
    }


def classify_server(name: str, spec: dict[str, Any], core_name: str) -> dict[str, Any]:
    command = str(spec.get("command", ""))
    args = [str(x) for x in spec.get("args", [])]
    env = spec.get("env") if isinstance(spec.get("env"), dict) else {}
    text = " ".join([name, command] + args).lower()
    is_core = name == core_name or "rgm_mcp_server.py" in text
    matched = None
    if not is_core:
        for adapter in external_adapters():
            probes = [adapter["id"]] + list(adapter.get("aliases", []))
            if any(str(probe).lower().replace("_", "-") in text.replace("_", "-") for probe in probes):
                matched = adapter["id"]
                break
    result = {
        "name": name,
        "classification": "first_party_core" if is_core else "adapter_only",
        "matched_adapter": matched,
        "command": command,
        "args": args,
        "has_rgm_root_env": bool(env.get("RGM_MCP_ROOT")),
        "not_completion_proof": not is_core,
    }
    if is_core:
        result["core_checks"] = {
            "uses_rgm_server_script": "rgm_mcp_server.py" in " ".join(args) or command.endswith("rgm_mcp_server.py"),
            "rgm_root_env": env.get("RGM_MCP_ROOT", ""),
        }
    return result


def host_check(config_path: str, core_name: str, root: str | None) -> tuple[dict[str, Any], int]:
    path = Path(config_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers")
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(servers, dict):
        return ({"schema_version": HOST_CHECK_SCHEMA, "config": str(path), "status": "FAIL", "errors": ["missing mcpServers object"]}, 1)
    classifications = [classify_server(str(name), spec if isinstance(spec, dict) else {}, core_name) for name, spec in servers.items()]
    core = [item for item in classifications if item["classification"] == "first_party_core"]
    if not core:
        errors.append(f"missing first-party core server: {core_name}")
    else:
        for item in core:
            checks = item.get("core_checks", {})
            if not checks.get("uses_rgm_server_script"):
                errors.append(f"core server {item['name']} does not point at rgm_mcp_server.py")
            root_env = str(checks.get("rgm_root_env", ""))
            if not root_env:
                errors.append(f"core server {item['name']} missing env.RGM_MCP_ROOT")
            elif root and Path(root_env).resolve() != Path(root).resolve():
                errors.append(f"core server {item['name']} RGM_MCP_ROOT mismatch: {root_env}")
    for item in classifications:
        if item["classification"] == "adapter_only" and item.get("matched_adapter") is None:
            warnings.append(f"server {item['name']} is adapter_only but not matched to a known registry profile")
    status = "PASS" if not errors else "FAIL"
    payload = {
        "schema_version": HOST_CHECK_SCHEMA,
        "generated_at": now_iso(),
        "config": str(path),
        "status": status,
        "core_name": core_name,
        "servers": classifications,
        "errors": errors,
        "warnings": warnings,
        "completion_requires": ["external verifier exit 0", "rgm_gate.py RGM_GATE=PASS"],
        "not_completion_proof": True,
    }
    return payload, 0 if status == "PASS" else 1


def emit_json(payload: dict[str, Any], out: str = "") -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if out:
        Path(out).write_text(text, encoding="utf-8")
    print(text, end="")


def cmd_registry(args: argparse.Namespace) -> int:
    emit_json(registry(), args.out)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        payload = plan(args.adapter, args.task, args.root)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    emit_json(payload, args.out)
    return 0


def cmd_host_check(args: argparse.Namespace) -> int:
    payload, code = host_check(args.config, args.core_name, args.root)
    emit_json(payload, args.out)
    print("<!--RGM_MCP_HOST_CHECK_BEGIN-->")
    print(f"MCP_HOST_CHECK={payload['status']}")
    print(f"CORE_NAME={args.core_name}")
    print(f"SERVERS={len(payload.get('servers', []))}")
    print("<!--RGM_MCP_HOST_CHECK_END-->")
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RGM external MCP adapter registry and host preflight")
    sub = parser.add_subparsers(dest="cmd", required=True)
    registry_p = sub.add_parser("registry", help="print external MCP adapter registry")
    registry_p.add_argument("--out", default="")
    registry_p.set_defaults(func=cmd_registry)
    plan_p = sub.add_parser("plan", help="print an adapter usage plan")
    plan_p.add_argument("--adapter", required=True)
    plan_p.add_argument("--task", required=True)
    plan_p.add_argument("--root", default=str(repo_root()))
    plan_p.add_argument("--out", default="")
    plan_p.set_defaults(func=cmd_plan)
    check_p = sub.add_parser("host-check", help="validate an MCP host config keeps RGM core first-party")
    check_p.add_argument("--config", required=True)
    check_p.add_argument("--core-name", default=DEFAULT_CORE_NAME)
    check_p.add_argument("--root", default="")
    check_p.add_argument("--out", default="")
    check_p.set_defaults(func=cmd_host_check)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

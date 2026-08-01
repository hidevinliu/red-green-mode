#!/usr/bin/env python3
"""Generate install profiles and explain MCP build-vs-buy strategy for RGM.

The RGM policy is first-party core, external adapters optional: the red-green
source of truth is the local ledger/verifier/gate; third-party MCP servers may
supply context but do not decide completion.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVER_NAME = "rgm-readonly-repo"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def server_script() -> Path:
    return Path(__file__).resolve().parent / "rgm_mcp_server.py"


def config_payload(name: str, python: str, root: str, allow_verify: bool, enable_write: bool = False) -> dict[str, object]:
    env = {"RGM_MCP_ROOT": root}
    if allow_verify:
        env["RGM_MCP_ALLOW_VERIFY"] = "1"
    if enable_write:
        env["RGM_MCP_ENABLE_WRITE"] = "1"
    return {
        "mcpServers": {
            name: {
                "command": python,
                "args": [str(server_script())],
                "env": env,
            }
        }
    }


def strategy_payload() -> dict[str, object]:
    return {
        "schema_version": "rgm-mcp-strategy.v1",
        "recommendation": "build_first_party_thin_mcp_server",
        "why_not_directly_depend_on_repoprompt": [
            "RepoPrompt CE MCP is optimized for RepoPrompt's app/workspace model and macOS context builder.",
            "red-green-mode needs ledger/verifier/gate as the source of truth, so completion semantics must stay first-party.",
            "third-party MCP servers can change independently; treating them as adapters avoids coupling your safety gates to their release cycle.",
        ],
        "what_to_reuse": [
            "Official MCP protocol/spec and optionally the official SDK after pinning a stable version.",
            "RepoPrompt-style concepts: CodeMap, context selection, reviewable handoff, worktree isolation, orchestration lanes.",
            "Other authors' MCP servers as replaceable context providers when they are already good at a domain, registered through rgm_mcp_adapters.py.",
        ],
        "ownership_model": {
            "core": "RGM first-party MCP server exposes your repo/context/verifier/gate primitives.",
            "adapters": "External MCP servers are called through rgm_mcp_adapters.py profiles and never become completion proof.",
            "write_boundary": "repo.patch.apply is disabled by default and only appears with RGM_MCP_ENABLE_WRITE=1; calls still require ledger PLAN_ONE_FIX/hypothesis + context_pack selected/risk/dependency boundaries and remain not-completion-proof.",
        },
    }


def cmd_config(args: argparse.Namespace) -> int:
    root = str(Path(args.root).resolve())
    payload = config_payload(args.name, args.python, root, args.allow_verify, args.enable_write)
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_strategy(args: argparse.Namespace) -> int:
    print(json.dumps(strategy_payload(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate RGM MCP install profile and strategy")
    sub = parser.add_subparsers(dest="cmd", required=True)
    cfg = sub.add_parser("config", help="print client mcpServers JSON")
    cfg.add_argument("--name", default=SERVER_NAME)
    cfg.add_argument("--python", default=sys.executable)
    cfg.add_argument("--root", default=str(repo_root()))
    cfg.add_argument("--allow-verify", action="store_true", help="enable repo.verify.run via env; still not completion proof")
    cfg.add_argument("--enable-write", action="store_true", help="expose repo.patch.apply via RGM_MCP_ENABLE_WRITE=1; still requires ledger/context/hypothesis and is not completion proof")
    cfg.add_argument("--out", default="")
    cfg.set_defaults(func=cmd_config)
    strat = sub.add_parser("strategy", help="explain first-party vs external MCP strategy")
    strat.set_defaults(func=cmd_strategy)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

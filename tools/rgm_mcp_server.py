#!/usr/bin/env python3
"""JSON-RPC stdio server for red-green-mode repo context tools.

This is a deliberately small MCP-compatible skeleton: by default it exposes
read-only repo context operations and the read-only explorer lane over
JSON-RPC 2.0 on stdio. Write tools are hidden unless RGM_MCP_ENABLE_WRITE=1,
and even then repo.patch.apply is guarded by Run Ledger + hypothesis +
Context Pack boundaries. Any tool output remains not-completion-proof.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any

import rgm_codemap

SERVER_NAME = "rgm-readonly-repo-mcp"
SERVER_VERSION = "0.18.0"
PROTOCOL_VERSION = "2025-11-25"


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def default_root(arguments: dict[str, Any]) -> Path:
    """Resolve the repo root from tool args, install-profile env, or cwd."""
    return Path(str(arguments.get("root") or os.environ.get("RGM_MCP_ROOT") or ".")).resolve()


def text_result(text: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def structured_result(payload: dict[str, Any]) -> dict[str, object]:
    """Return MCP-compatible tool output with both text and structuredContent.

    The MCP tools spec recommends keeping serialized JSON in a TextContent
    block for backwards compatibility when returning structuredContent.
    """
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)}],
        "structuredContent": payload,
        "isError": False,
    }


def error_result(message: str) -> dict[str, object]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def write_tools_enabled() -> bool:
    return os.environ.get("RGM_MCP_ENABLE_WRITE") == "1"


def resolve_inside(root: Path, rel: str) -> Path:
    root = root.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes root: {rel}") from exc
    return target


def resolve_arg_path_inside(root: Path, value: str, label: str) -> Path:
    if not value.strip():
        raise ValueError(f"{label} is required")
    root = root.resolve()
    raw = Path(value)
    target = raw.resolve() if raw.is_absolute() else (root / raw).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes root: {value}") from exc
    return target


def load_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_repo_path(value: str | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw or raw == "None":
        return None
    if raw == "/dev/null":
        return None
    if raw.startswith("a/") or raw.startswith("b/"):
        raw = raw[2:]
    raw = raw.replace("\\", "/")
    while raw.startswith("./"):
        raw = raw[2:]
    path = PurePosixPath(raw)
    if path.is_absolute():
        raise ValueError(f"absolute paths are not allowed in patches/context: {value}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"path traversal is not allowed: {value}")
    if path.parts and path.parts[0] == ".git":
        raise ValueError(".git paths are not allowed")
    return path.as_posix()


def context_path_items(items: Any) -> set[str]:
    paths: set[str] = set()
    if not isinstance(items, list):
        return paths
    for item in items:
        raw: str | None
        if isinstance(item, dict):
            raw = item.get("path")
        else:
            raw = str(item)
        normalized = normalize_repo_path(raw)
        if normalized:
            paths.add(normalized)
    return paths


def context_allowed_paths(context_pack: dict[str, Any]) -> set[str]:
    allowed = context_path_items(context_pack.get("selected_files", []))
    allowed.update(context_path_items(context_pack.get("risk_files", [])))
    edges = context_pack.get("dependency_edges") or []
    for edge in edges:
        if isinstance(edge, dict):
            for key in ("from", "to", "source", "target"):
                normalized = normalize_repo_path(edge.get(key))
                if normalized:
                    allowed.add(normalized)
    return {path for path in allowed if path}


def parse_diff_path_marker(marker: str) -> str | None:
    """Parse a ---/+++ diff marker path and normalize it to a repo path."""
    token = marker.strip()
    if "\t" in token:
        token = token.split("\t", 1)[0]
    elif " " in token:
        token = token.split(" ", 1)[0]
    return normalize_repo_path(token)


ANTI_CHEAT_PATTERNS = [
    re.compile(r"\bpytestmark\s*=.*\bskip\b"),
    re.compile(r"\bpytest\.mark\.(skip|xfail)\b"),
    re.compile(r"\b(skip|xfail)\s*\("),
    re.compile(r"\bassert\s+True\b"),
    re.compile(r"#\s*type:\s*ignore\b"),
    re.compile(r"@ts-ignore\b"),
    re.compile(r"eslint-disable\b"),
]


def is_test_path(path: str | None) -> bool:
    if not path:
        return False
    name = PurePosixPath(path).name
    return path.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py")


def parse_patch_touched_files(patch: str) -> list[str]:
    if not patch.strip():
        raise ValueError("patch is required")
    touched: set[str] = set()
    old_path: str | None = None
    current_new_path: str | None = None
    saw_diff = False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            saw_diff = True
            parts = line.split()
            if len(parts) >= 4:
                left = normalize_repo_path(parts[2])
                right = normalize_repo_path(parts[3])
                if left and right and left != right:
                    raise ValueError("rename patches are not allowed")
            old_path = None
            current_new_path = None
            continue
        if line.startswith(("rename from ", "rename to ")):
            raise ValueError("rename patches are not allowed")
        if line.startswith("deleted file mode"):
            raise ValueError("deletion patches are not allowed")
        if line.startswith(("old mode ", "new mode ")):
            raise ValueError("chmod/mode-change patches are not allowed")
        if line.startswith("--- "):
            old_path = parse_diff_path_marker(line[4:])
            continue
        if line.startswith("+++ "):
            new_path = parse_diff_path_marker(line[4:])
            if old_path and new_path and old_path != new_path:
                raise ValueError("rename patches are not allowed")
            if old_path and new_path is None:
                raise ValueError("deletion patches are not allowed")
            current_new_path = new_path or old_path
            if current_new_path:
                touched.add(current_new_path)
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if is_test_path(current_new_path):
                added = line[1:]
                if any(pattern.search(added) for pattern in ANTI_CHEAT_PATTERNS):
                    raise ValueError(f"anti-cheat pattern rejected in {current_new_path}")
    if not saw_diff or not touched:
        raise ValueError("patch must be a unified diff with file headers")
    return sorted(touched)


def validate_ledger_for_write(root: Path, ledger_arg: str, hypothesis: str) -> Path:
    if not ledger_arg.strip():
        raise ValueError("ledger is required")
    if not hypothesis.strip():
        raise ValueError("hypothesis is required")
    ledger_path = resolve_arg_path_inside(root, ledger_arg, "ledger")
    if not ledger_path.exists():
        raise ValueError(f"ledger does not exist: {ledger_arg}")
    data = load_json_file(ledger_path)
    entries = data.get("entries") or []
    if not entries:
        raise ValueError("ledger has no entries; record PLAN_ONE_FIX before applying a patch")
    last = entries[-1]
    state = str(last.get("state", ""))
    next_state = str(last.get("next", ""))
    ledger_hypothesis = str(last.get("hypothesis", "")).strip()
    if state != "PLAN_ONE_FIX" and next_state != "PLAN_ONE_FIX":
        raise ValueError("ledger must be at PLAN_ONE_FIX (state or next) before repo.patch.apply")
    if not ledger_hypothesis:
        raise ValueError("ledger hypothesis is required before repo.patch.apply")
    return ledger_path


def git_diff_summary(root: Path) -> dict[str, str]:
    try:
        stat = subprocess.run(["git", "diff", "--stat"], cwd=str(root), capture_output=True, text=True, timeout=10)
        names = subprocess.run(["git", "diff", "--name-only"], cwd=str(root), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"stat": f"(git diff unavailable: {exc})", "name_only": ""}
    if stat.returncode != 0 or names.returncode != 0:
        return {
            "stat": (stat.stderr or stat.stdout or "(git diff unavailable)").strip(),
            "name_only": (names.stderr or names.stdout or "").strip(),
        }
    return {"stat": stat.stdout.strip() or "(empty)", "name_only": names.stdout.strip() or "(empty)"}


def tool_codemap_build(arguments: dict[str, Any]) -> dict[str, object]:
    root = default_root(arguments)
    out_dir = Path(str(arguments.get("out_dir", root / ".rgm")))
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    max_file_bytes = int(arguments.get("max_file_bytes", 200_000))
    no_cache = bool(arguments.get("no_cache", False))
    refresh_cache = bool(arguments.get("refresh_cache", False))
    cache_dir_arg = str(arguments.get("cache_dir", ""))
    cache_dir = None if no_cache else rgm_codemap.cache_dir_from_args(str(out_dir), cache_dir_arg)
    codemap = rgm_codemap.build_codemap(
        root,
        max_file_bytes=max_file_bytes,
        cache_dir=cache_dir,
        use_cache=not no_cache,
        refresh_cache=refresh_cache,
    )
    json_path, md_path = rgm_codemap.write_codemap(codemap, out_dir)
    return structured_result({
        "schema_version": codemap.schema_version,
        "codemap_json": str(json_path),
        "codemap_md": str(md_path),
        "files": len(codemap.files),
        "symbols": len(codemap.symbol_index),
        "dependency_edges": len(codemap.dependency_edges),
        "cache": codemap.cache,
        "not_completion_proof": True,
    })


def tool_context_select(arguments: dict[str, Any]) -> dict[str, object]:
    root = default_root(arguments)
    task = str(arguments.get("task", "")).strip()
    if not task:
        return error_result("task is required")
    out_dir = Path(str(arguments.get("out_dir", root / ".rgm")))
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    max_file_bytes = int(arguments.get("max_file_bytes", 200_000))
    max_files = int(arguments.get("max_files", 24))
    max_context_tokens = int(arguments.get("max_context_tokens", 24_000))
    includes = [str(x) for x in arguments.get("include", [])]
    verify = [str(x) for x in arguments.get("verify", [])]
    codemap_path = Path(str(arguments.get("codemap", out_dir / "codemap.json")))
    if not codemap_path.is_absolute():
        codemap_path = root / codemap_path
    if codemap_path.exists():
        codemap = rgm_codemap.load_codemap(codemap_path)
    else:
        cache_dir = rgm_codemap.cache_dir_from_args(str(out_dir), str(arguments.get("cache_dir", "")))
        codemap = rgm_codemap.build_codemap(root, max_file_bytes=max_file_bytes, cache_dir=cache_dir)
        codemap_path, _ = rgm_codemap.write_codemap(codemap, out_dir)
    pack = rgm_codemap.build_selection_pack(
        root=root,
        codemap=codemap,
        codemap_path=codemap_path,
        task=task,
        includes=includes,
        verify=verify,
        max_files=max_files,
        max_file_bytes=max_file_bytes,
        max_context_tokens=max_context_tokens,
    )
    json_path, md_path = rgm_codemap.write_selection_pack(pack, out_dir)
    return structured_result({
        "context_pack_json": str(json_path),
        "context_pack_md": str(md_path),
        "selected_files": pack["selected_files"],
        "test_commands": pack["test_commands"],
        "unknowns": pack["unknowns"],
        "not_completion_proof": True,
    })


def tool_read_slice(arguments: dict[str, Any]) -> dict[str, object]:
    root = default_root(arguments)
    rel = str(arguments.get("path", "")).strip()
    if not rel:
        return error_result("path is required")
    context_pack_path = str(arguments.get("context_pack", "")).strip()
    allow_unselected = bool(arguments.get("allow_unselected", False))
    if context_pack_path:
        cp_path = Path(context_pack_path)
        if not cp_path.is_absolute():
            cp_path = root / cp_path
        allowed = context_allowed_paths(load_json_file(cp_path))
        normalized = rgm_codemap.normalize_include_path(rel)
        if normalized not in allowed and not allow_unselected:
            return error_result(f"{normalized} is outside selected/risk/dependency context; set allow_unselected with ledger justification")
    elif not allow_unselected:
        return error_result("context_pack is required unless allow_unselected=true")
    target = resolve_inside(root, rel)
    if not target.exists() or not target.is_file():
        return error_result(f"file not found: {rel}")
    start_line = max(1, int(arguments.get("start_line", 1)))
    max_lines = min(max(1, int(arguments.get("max_lines", 80))), 300)
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    end = min(len(lines), start_line + max_lines - 1)
    numbered = [f"{idx}: {lines[idx - 1]}" for idx in range(start_line, end + 1)]
    return text_result("\n".join(numbered))


def tool_diff_summary(arguments: dict[str, Any]) -> dict[str, object]:
    root = default_root(arguments)
    try:
        stat = subprocess.run(["git", "diff", "--stat"], cwd=str(root), capture_output=True, text=True, timeout=10)
        names = subprocess.run(["git", "diff", "--name-only"], cwd=str(root), capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return error_result(f"git diff failed: {exc}")
    text = "## git diff --stat\n" + (stat.stdout.strip() or "(empty)")
    text += "\n\n## git diff --name-only\n" + (names.stdout.strip() or "(empty)")
    return text_result(text)



def _is_allowed_verifier_command(argv: list[str]) -> bool:
    """Allow only verifier-like commands for repo.verify.run.

    RGM_MCP_ALLOW_VERIFY intentionally does not mean arbitrary process exec.
    The tool is still marked not-completion-proof, and final green still needs
    rgm_gate.py, but command execution is constrained to common test/type/lint
    frontends.
    """
    if not argv:
        return False
    cmd = Path(argv[0]).name
    if cmd in {"pytest", "ruff", "mypy", "pyright", "tsc", "vitest", "jest", "eslint"}:
        return True
    if cmd in {"python", "python3"} and len(argv) >= 3 and argv[1] == "-m":
        return argv[2] in {"pytest", "ruff", "mypy", "pyright"}
    if cmd == "go" and len(argv) >= 2:
        return argv[1] == "test"
    if cmd == "cargo" and len(argv) >= 2:
        return argv[1] in {"test", "check", "clippy"}
    if cmd in {"npm", "pnpm", "yarn"}:
        if len(argv) >= 2 and argv[1] == "test":
            return True
        if len(argv) >= 3 and argv[1] == "run":
            script = argv[2]
            return script in {"test", "test:unit", "test:integration", "lint", "typecheck", "check", "build"}
    if cmd == "npx" and len(argv) >= 2:
        return argv[1] in {"tsc", "vitest", "jest", "eslint", "tsx"}
    return False

def tool_verify_run(arguments: dict[str, Any]) -> dict[str, object]:
    if os.environ.get("RGM_MCP_ALLOW_VERIFY") != "1":
        return error_result("repo.verify.run is disabled by default; set RGM_MCP_ALLOW_VERIFY=1 and still require rgm_gate.py before FULL_GREEN")
    root = default_root(arguments)
    command = str(arguments.get("command", "")).strip()
    if not command:
        return error_result("command is required")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return error_result(f"invalid command syntax: {exc}")
    if not _is_allowed_verifier_command(argv):
        return error_result(
            "repo.verify.run only allows verifier commands (pytest/ruff/mypy/tsc/vitest/jest/eslint/go test/cargo test/npm test). "
            "Refusing arbitrary command execution."
        )
    timeout = min(max(1, int(arguments.get("timeout_seconds", 120))), 1800)
    proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, timeout=timeout)
    out = (proc.stdout + "\n" + proc.stderr).strip()
    if len(out) > 8_000:
        out = out[:8_000] + "\n...[truncated]"
    return structured_result({"exit_code": proc.returncode, "output": out, "completion_proof": False})


def tool_patch_apply(arguments: dict[str, Any]) -> dict[str, object]:
    root = default_root(arguments)
    patch = str(arguments.get("patch", ""))
    ledger_arg = str(arguments.get("ledger", "")).strip()
    hypothesis = str(arguments.get("hypothesis", "")).strip()
    context_arg = str(arguments.get("context_pack", "")).strip()
    try:
        validate_ledger_for_write(root, ledger_arg, hypothesis)
        if not context_arg:
            raise ValueError("context_pack is required")
        context_path = resolve_arg_path_inside(root, context_arg, "context_pack")
        if not context_path.exists():
            raise ValueError(f"context_pack does not exist: {context_arg}")
        touched = parse_patch_touched_files(patch)
        allowed = context_allowed_paths(load_json_file(context_path))
        outside = [path for path in touched if path not in allowed]
        if outside:
            raise ValueError(
                "patch target(s) outside selected/risk/dependency context: "
                + ", ".join(outside)
            )
        for rel in touched:
            resolve_inside(root, rel)
        check = subprocess.run(
            ["git", "apply", "--check", "--whitespace=nowarn", "-"],
            input=patch,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
        )
        if check.returncode != 0:
            return error_result(("git apply --check failed: " + (check.stderr or check.stdout)).strip())
        apply = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            input=patch,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
        )
        if apply.returncode != 0:
            return error_result(("git apply failed: " + (apply.stderr or apply.stdout)).strip())
        return structured_result({
            "applied": True,
            "touched_files": touched,
            "diff_summary": git_diff_summary(root),
            "not_completion_proof": True,
            "completion_proof": False,
            "must_still_run": ["external verifier", "tools/rgm_gate.py"],
        })
    except (ValueError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired) as exc:
        return error_result(str(exc))


def tool_explore_read_only(arguments: dict[str, Any]) -> dict[str, object]:
    root = default_root(arguments)
    task = str(arguments.get("task", "")).strip()
    if not task:
        return error_result("task is required")
    out_dir = Path(str(arguments.get("out_dir", root / ".rgm")))
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    max_file_bytes = int(arguments.get("max_file_bytes", 200_000))
    codemap = rgm_codemap.build_codemap(root, max_file_bytes=max_file_bytes, cache_dir=out_dir / "codemap-artifacts")
    codemap_path, _ = rgm_codemap.write_codemap(codemap, out_dir)
    pack = rgm_codemap.build_selection_pack(
        root=root,
        codemap=codemap,
        codemap_path=codemap_path,
        task=task,
        includes=[str(x) for x in arguments.get("include", [])],
        verify=[str(x) for x in arguments.get("verify", [])],
        max_files=int(arguments.get("max_files", 12)),
        max_file_bytes=max_file_bytes,
        max_context_tokens=int(arguments.get("max_context_tokens", 16_000)),
    )
    context_path, _ = rgm_codemap.write_selection_pack(pack, out_dir)
    top_files = [item["path"] for item in pack["selected_files"][:5]]
    summary = {
        "schema_version": "rgm-readonly-explorer.v1",
        "task": task,
        "codemap_json": str(codemap_path),
        "context_pack_json": str(context_path),
        "selected_files": top_files,
        "risk_files": pack["risk_files"],
        "test_commands": pack["test_commands"],
        "unknowns": pack["unknowns"],
        "cache": codemap.cache,
        "may_write_code": False,
        "not_completion_proof": True,
    }
    return structured_result(summary)


def tool_host_status(arguments: dict[str, Any]) -> dict[str, object]:
    payload = {
        "schema_version": "rgm-mcp-host-status.v1",
        "server_name": SERVER_NAME,
        "server_version": SERVER_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "ownership_strategy": "first_party_thin_server",
        "dependency_strategy": {
            "core": "stdlib JSON-RPC stdio server maintained inside red-green-mode",
            "official_sdk": "optional adapter after pinning a stable mcp package; do not block core red-green flow",
            "foreign_servers": "allowed as adapters/context providers, never as the red-green source of truth",
        },
        "write_tools_exposed_by_default": False,
        "write_tools_enable_env": "RGM_MCP_ENABLE_WRITE=1",
        "write_tool_guards": ["ledger", "PLAN_ONE_FIX", "hypothesis", "context_pack selected/risk/dependency boundary"],
        "completion_requires": ["external verifier exit 0", "rgm_gate.py RGM_GATE=PASS"],
        "not_completion_proof": True,
    }
    return structured_result(payload)


TOOLS: dict[str, dict[str, Any]] = {
    "mcp.host_status": {
        "title": "RGM MCP Host Status",
        "description": "Explain first-party vs external MCP strategy, protocol version, and completion boundaries.",
        "inputSchema": {"type": "object", "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "handler": tool_host_status,
    },
    "repo.codemap.build": {
        "title": "Build RGM CodeMap",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "description": "Build a content-addressed CodeMap artifact set and .rgm/codemap.json/md.",
        "inputSchema": {"type": "object", "properties": {"root": {"type": "string"}, "out_dir": {"type": "string"}, "refresh_cache": {"type": "boolean"}}},
        "handler": tool_codemap_build,
    },
    "repo.context.select": {
        "title": "Select RGM Context",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "description": "Select task context from CodeMap and write .rgm/context-pack.json/md.",
        "inputSchema": {"type": "object", "properties": {"root": {"type": "string"}, "task": {"type": "string"}, "verify": {"type": "array", "items": {"type": "string"}}}, "required": ["task"]},
        "handler": tool_context_select,
    },
    "repo.read.slice": {
        "title": "Read File Slice",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "description": "Read a bounded file slice, preferably inside selected context.",
        "inputSchema": {"type": "object", "properties": {"root": {"type": "string"}, "path": {"type": "string"}, "context_pack": {"type": "string"}, "start_line": {"type": "integer"}, "max_lines": {"type": "integer"}}, "required": ["path"]},
        "handler": tool_read_slice,
    },
    "repo.diff.summary": {
        "title": "Summarize Git Diff",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "description": "Summarize current git diff stat and touched files.",
        "inputSchema": {"type": "object", "properties": {"root": {"type": "string"}}},
        "handler": tool_diff_summary,
    },
    "repo.verify.run": {
        "title": "Run Verifier",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "description": "Run an explicitly enabled verifier command; never completion proof by itself.",
        "inputSchema": {"type": "object", "properties": {"root": {"type": "string"}, "command": {"type": "string"}, "timeout_seconds": {"type": "integer"}}, "required": ["command"]},
        "handler": tool_verify_run,
    },
    "agent.explore.read_only": {
        "title": "Read-only Explorer",
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
        "description": "Read-only exploration lane: build CodeMap, select context, summarize risks/tests/unknowns; cannot edit or complete.",
        "inputSchema": {"type": "object", "properties": {"root": {"type": "string"}, "task": {"type": "string"}, "max_files": {"type": "integer"}}, "required": ["task"]},
        "handler": tool_explore_read_only,
    },
}


WRITE_TOOLS: dict[str, dict[str, Any]] = {
    "repo.patch.apply": {
        "title": "Apply Bounded Patch",
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
        "description": (
            "Apply a unified diff only when RGM_MCP_ENABLE_WRITE=1 and the call "
            "has ledger PLAN_ONE_FIX intent, non-empty hypothesis, and a context_pack "
            "selected/risk/dependency boundary. Returns a diff summary; never completion proof."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string"},
                "patch": {"type": "string"},
                "ledger": {"type": "string"},
                "hypothesis": {"type": "string"},
                "context_pack": {"type": "string"},
            },
            "required": ["patch", "ledger", "hypothesis", "context_pack"],
            "additionalProperties": False,
        },
        "handler": tool_patch_apply,
    },
}


def active_tools() -> dict[str, dict[str, Any]]:
    if write_tools_enabled():
        return {**TOOLS, **WRITE_TOOLS}
    return TOOLS


def tool_list() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name, spec in active_tools().items():
        item = {
            "name": name,
            "title": spec.get("title", name),
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
        }
        if "annotations" in spec:
            item["annotations"] = spec["annotations"]
        result.append(item)
    return result


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    req_id = request.get("id")
    method = request.get("method")
    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "capabilities": {"tools": {"listChanged": False}},
            }
        elif method == "notifications/initialized":
            return None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_list()}
        elif method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            tools = active_tools()
            if name not in tools:
                raise JsonRpcError(-32602, f"Unknown tool: {name}")
            result = tools[str(name)]["handler"](arguments)
        elif method in {"shutdown", "exit"}:
            result = {}
        else:
            raise JsonRpcError(-32601, f"unsupported method: {method}")
        if req_id is None:
            return None
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except Exception as exc:
        if req_id is None:
            return None
        code = exc.code if isinstance(exc, JsonRpcError) else -32000
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": str(exc)}}


def serve() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}) + "\n")
            sys.stdout.flush()
            continue
        response = handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True) + "\n")
            sys.stdout.flush()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RGM read-only repo MCP stdio server")
    parser.add_argument("--list-tools", action="store_true", help="print tool list JSON and exit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.list_tools:
        print(json.dumps({"tools": tool_list()}, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())

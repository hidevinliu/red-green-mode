#!/usr/bin/env python3
"""Acceptance-contract validator + attestation for red-green-mode v0.10.0 (S4).

acceptance-contract.json is the shared verification SSOT that flows
PRD authoring -> test-driven-development -> red-green-mode. Each baton fills its
own column; red-green's rgm_gate enforces full coverage at REPORT time.

Subcommands:
  validate      schema-check the contract (+ behavior-lint warn on path:line rot)
  attest        lock the verifier-command set with a SHA-256 over (id, verifier)
  verify-attest recompute the hash and reject if a verifier was changed without
                re-attesting (adapted from planning-with-files: stops the judge from being
                swapped out for `echo PASS`)

Exit codes: 0 = ok, 1 = invalid / tampered, 2 = can't read / no attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED = ("id", "scenario", "check_type", "expect", "status", "depends_on")
CHECK_TYPES = {"test", "smoke", "lint", "constraint"}
STATUSES = {"todo", "red", "green", "uncovered", "missing"}
PATH_LINE_RE = re.compile(r"[\w./-]+\.[A-Za-z]\w*:\d+")  # foo.py:42 — rot risk in an assertion
# The seven hard gates of the operator autonomy policy, including "touches another repo's
# runtime", which real runs turned up: an item flagged with `gate` in the contract is NOT
# autonomous. The loop works through every non-gate item on its own and stops only at gates,
# reporting that gate's question when it does (asking one concrete question, not dumping a menu).
GATE_KINDS = {
    "cross-repo-runtime",  # touches another repo's runtime (e.g. wiring up an end-to-end run)
    "destructive",         # deleting / bulk-moving / overwriting conflicting content
    "protected-config",    # editing lint thresholds / settings.json and other protected config
    "external-send",       # DOCX export / sending anything externally
    "privacy-archive",     # opening a privacy-sensitive or personal archive
    "append-only-ledger",  # an append-only ledger the agent may not rewrite
    "target-confirm",      # confirming the target once before the autonomous loop starts
}


def _load(path_str: str) -> tuple[dict[str, Any] | None, int]:
    p = Path(path_str)
    if not p.exists():
        print(f"BLOCKED: contract not found: {p}", file=sys.stderr)
        return None, 2
    try:
        return json.loads(p.read_text(encoding="utf-8")), 0
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: cannot parse contract: {exc}", file=sys.stderr)
        return None, 2


def _attest_hash(items: list[dict[str, Any]]) -> str:
    # lock the whole judging semantics per item — not just verifier, also check_type and
    # expect — so weakening expectation or swapping check kind also trips verify-attest.
    pairs = sorted(
        (str(it.get("id", "")), str(it.get("check_type") or ""),
         str(it.get("expect") or ""), str(it.get("verifier") or ""))
        for it in items)
    blob = json.dumps(pairs, ensure_ascii=False, sort_keys=True)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _has_cycle(items: list[dict[str, Any]]) -> bool:
    graph = {it["id"]: list(it.get("depends_on") or []) for it in items if "id" in it}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in graph}

    def dfs(n: str) -> bool:
        color[n] = GRAY
        for m in graph.get(n, []):
            if m not in color:
                continue
            if color[m] == GRAY or (color[m] == WHITE and dfs(m)):
                return True
        color[n] = BLACK
        return False

    return any(color[n] == WHITE and dfs(n) for n in graph)


def _schema_errors(data: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Shared by `validate` and `attest` so we never stamp
    an attestation onto a malformed contract (code review P2-8)."""
    errors: list[str] = []
    warnings: list[str] = []
    if data.get("version") != "1":
        errors.append(f'version must be "1", got {data.get("version")!r}')

    # P1 - optional scope declaration: distinguishes "definition of done for the whole project"
    # from "milestone N/M". Without it GATE cannot tell "contract is green" apart from "project is
    # done", so the loop finishes the moment one milestone goes green and the agent falls back to
    # dumping a menu at the user (the root cause).
    scope = data.get("scope")
    if scope is not None and not isinstance(scope, str):
        errors.append("scope must be a string (e.g. 'project' or 'milestone-1/3')")
    nm = data.get("next_milestone")
    if nm is not None and not isinstance(nm, str):
        errors.append("next_milestone must be a string")
    if isinstance(scope, str) and scope.startswith("milestone") and not (nm and nm.strip()):
        warnings.append("scope is a milestone but next_milestone is empty; once the loop goes green it "
                        "has nowhere to advance to and will stop to ask you what is next. "
                        "Either fill in next_milestone or set scope to 'project'")

    items = data.get("items")
    if not isinstance(items, list):
        errors.append("items must be a list")
        items = []

    seen_ids: set[str] = set()
    for i, it in enumerate(items):
        for key in REQUIRED:
            if key not in it:
                errors.append(f"item[{i}] missing required field {key!r}")
        if it.get("check_type") not in CHECK_TYPES:
            errors.append(f"item[{i}] check_type {it.get('check_type')!r} not in {sorted(CHECK_TYPES)}")
        if it.get("status") not in STATUSES:
            errors.append(f"item[{i}] status {it.get('status')!r} not in {sorted(STATUSES)}")
        iid = it.get("id")
        if iid in seen_ids:
            errors.append(f"duplicate id {iid!r}")
        if iid is not None:
            seen_ids.add(iid)
        if not isinstance(it.get("depends_on", []), list):
            errors.append(f"item[{i}] depends_on must be a list")
        gate = it.get("gate")  # P2 - optional hard gate: not autonomous; on stopping, report its question
        if gate is not None:
            if not isinstance(gate, dict):
                errors.append(f"item[{i}].gate must be an object {{kind, question}}")
            else:
                if gate.get("kind") not in GATE_KINDS:
                    errors.append(f"item[{i}].gate.kind {gate.get('kind')!r} not in {sorted(GATE_KINDS)}")
                if not str(gate.get("question") or "").strip():
                    errors.append(f"item[{i}].gate needs a non-empty 'question' (the single factual question to ask the user when stopping at this gate)")
        for field in ("scenario", "expect"):  # behavior-lint: pinning to path:line rots under refactoring
            m = PATH_LINE_RE.search(str(it.get(field, "")))
            if m:
                warnings.append(f"item[{i}].{field} pins the line number {m.group(0)!r}, which rots under refactoring; assert on behaviour instead")

    for it in items:  # depends_on referential integrity + acyclicity
        for dep in it.get("depends_on", []) or []:
            if dep not in seen_ids:
                errors.append(f"item {it.get('id')!r} depends_on unknown id {dep!r}")
    if items and _has_cycle([it for it in items if "id" in it]):
        errors.append("depends_on graph has a cycle")
    return errors, warnings


def cmd_validate(args: argparse.Namespace) -> int:
    data, code = _load(args.file)
    if data is None:
        return code
    errors, warnings = _schema_errors(data)
    items = data.get("items") if isinstance(data.get("items"), list) else []

    for w in warnings:
        print(f"WARN: {w}")
    if errors:
        for e in errors:
            print(f"INVALID: {e}", file=sys.stderr)
        return 1
    print(json.dumps({"valid": True, "items": len(items), "warnings": len(warnings)}, ensure_ascii=False))
    return 0


def cmd_attest(args: argparse.Namespace) -> int:
    data, code = _load(args.file)
    if data is None:
        return code
    errors, _ = _schema_errors(data)  # don't stamp an attestation onto a malformed contract
    if errors:
        for e in errors:
            print(f"INVALID (refusing to attest): {e}", file=sys.stderr)
        return 1
    items = data.get("items") or []
    data["attestation"] = {
        "hash": _attest_hash(items),
        "attested_at": datetime.now(timezone.utc).isoformat(),
    }
    Path(args.file).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"attested": True, "hash": data["attestation"]["hash"]}, ensure_ascii=False))
    return 0


def cmd_verify_attest(args: argparse.Namespace) -> int:
    data, code = _load(args.file)
    if data is None:
        return code
    att = data.get("attestation") or {}
    stored = att.get("hash")
    if not stored:
        print("BLOCKED: contract has no attestation; run `attest` first", file=sys.stderr)
        return 2
    current = _attest_hash(data.get("items") or [])
    if current != stored:
        print(f"TAMPERED: verifier commands changed since attestation "
              f"(stored {stored[:23]}…, now {current[:23]}…); re-attest explicitly", file=sys.stderr)
        return 1
    print(json.dumps({"attested": True, "match": True}, ensure_ascii=False))
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    """Dependency-aware readiness frontier (adapted from planning-with-files DependsOn).

    Classifies non-green items into ready (all deps green -> actionable now) vs
    blocked (waiting on a non-green dep). With --no-progress (stall-check already
    fired), an empty ready-set while items remain = stuck -> exit 1 so the loop
    reports STOPPED_NO_PROGRESS instead of spinning.
    """
    data, code = _load(args.file)
    if data is None:
        return code
    items = data.get("items") or []
    status = {it.get("id"): it.get("status") for it in items}
    non_green = [it for it in items if it.get("status") != "green"]
    # P2 - hard gates listed separately: not autonomous. The loop may only stop here, and
    # when it does it reports that gate's question (one concrete question, not a menu of options).
    gates = [{"id": it.get("id"), "kind": it["gate"].get("kind"),
              "question": it["gate"].get("question")}
             for it in non_green if isinstance(it.get("gate"), dict)]
    actionable = [it for it in non_green if not isinstance(it.get("gate"), dict)]
    ready: list[str] = []
    blocked: list[dict[str, Any]] = []
    for it in actionable:
        waiting = [d for d in (it.get("depends_on") or []) if status.get(d) != "green"]
        (blocked.append({"id": it.get("id"), "waiting_on": waiting}) if waiting
         else ready.append(it.get("id")))
    print(json.dumps({"all_green": not non_green, "ready": ready, "blocked": blocked, "gates": gates},
                     ensure_ascii=False))
    # "stuck" only counts items that should have advanced autonomously: having nothing left but
    # hard gates (actionable empty) is not spinning, it is legitimately stopping at a gate to ask.
    if args.no_progress and actionable and not ready:
        return 1  # stalled + every remaining actionable item blocked by deps = stuck
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="acceptance-contract validator + attestation")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, func, helptext in (
        ("validate", cmd_validate, "schema-check the contract"),
        ("attest", cmd_attest, "lock verifier commands with a SHA-256 attestation"),
        ("verify-attest", cmd_verify_attest, "reject if a verifier changed without re-attesting"),
    ):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--file", required=True, help="path to acceptance-contract.json")
        sp.set_defaults(func=func)
    rd = sub.add_parser("ready", help="dependency-aware ready/blocked frontier (depends_on)")
    rd.add_argument("--file", required=True, help="path to acceptance-contract.json")
    rd.add_argument("--no-progress", action="store_true",
                    help="stall-check already fired; empty ready-set => stuck (exit 1)")
    rd.set_defaults(func=cmd_ready)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

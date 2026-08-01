#!/usr/bin/env python3
"""Small Run Ledger helper for red-green-mode.

Usage examples:
  python tools/rgm_ledger.py init --task "Fix parser" --workspace /tmp/proj --out ledger.json
  python tools/rgm_ledger.py add --out ledger.json --state BASELINE --command "pytest -q" --label TASK_RED --result "3 failed" --red-count 3
  python tools/rgm_ledger.py stall-check --out ledger.json --patience 5   # exit 1 if the run has stopped making progress
  python tools/rgm_ledger.py report --out ledger.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_STATES = {
    "CONTEXT_PACK", "PRECHECK", "BASELINE", "TRIAGE", "PLAN_ONE_FIX", "INNER_VERIFY",
    "OUTER_VERIFY", "SMOKE_ASSERT", "QUALITY_REVIEW", "PRD_COVERAGE", "GATE", "REPORT",
    "BLOCKED_BY_INFRA", "TEST_QUESTION", "FLAKY", "STOPPED_NO_PROGRESS",
}
VALID_LABELS = {
    "", "TASK_RED", "BASELINE_RED", "ENV_RED", "TEST_QUESTION", "FLAKY",
    "BLOCKED_BY_INFRA", "FULL_GREEN", "TASK_GREEN_WITH_BASELINE_RED",
    "STOPPED_NO_PROGRESS",
}
# The four canonical completion states a run can finish in (see SKILL.md,
# "起手基线 + 完成状态" — opening baseline + completion states). cmd_finish validates against these so a typo'd
# completion state can't silently land in the ledger and the final report.
VALID_COMPLETION_STATES = {
    "FULL_GREEN", "TASK_GREEN_WITH_BASELINE_RED",
    "BLOCKED_BY_INFRA", "STOPPED_NO_PROGRESS",
}
# Execution modes (see the long-run/parallel design note). `single` keeps the
# v0.6.x single-agent behavior; `longrun`/`parallel` opt into the ContextWarden
# discipline layer and the worktree parallel sub-mode.
VALID_MODES = {"single", "longrun", "parallel"}
# Window-watchdog zones emitted by rgm_window.py.
VALID_ZONES = {"green", "amber", "red"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_elapsed(seconds: float) -> str:
    """Human-readable elapsed time, e.g. '1h 23m 45s' / '12m 03s' / '8s'."""
    total = int(round(seconds))
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def compute_duration(data: dict[str, Any]) -> str | None:
    """Wall-clock task duration from started_at -> final.completed_at.

    Returns a human string, or None when either timestamp is missing/unparseable.
    Duration is derived (never persisted) so it stays correct for old ledgers too.
    """
    start = data.get("started_at")
    end = (data.get("final") or {}).get("completed_at")
    if not start or not end:
        return None
    try:
        t0 = datetime.fromisoformat(start)
        t1 = datetime.fromisoformat(end)
    except ValueError:
        return None
    return format_elapsed((t1 - t0).total_seconds())


def evaluate_stall(red_counts: list[int], patience: int = 5) -> dict[str, Any]:
    """Decide whether a red-green run has stopped making progress.

    Input is the ordered list of failing-check counts, one per *measured*
    verify round (the rounds that logged --red-count). The skill preaches
    "the judge must be external, not the agent's gut" — so the stop-on-stall
    rule is computed here and returned as an exit code, instead of the agent
    eyeballing the trend.

    Rule: a run STALLs when it has gone `patience` rounds without setting a new
    lowest red-count — i.e. its best result is now `patience`+ rounds old. This
    is the computable form of "stop after N consecutive rounds with no improvement".
    Reaching 0 red is GREEN,
    never a stall. Too few rounds to judge yet -> INSUFFICIENT_DATA.

    Returns {"verdict", "reason", "exit_code"}:
      exit_code 0 -> keep looping (PROGRESSING / GREEN / INSUFFICIENT_DATA)
      exit_code 1 -> STALL: stop and hand back to a human.
    """
    if patience < 1:
        patience = 1
    n = len(red_counts)
    if n == 0:
        return {"verdict": "INSUFFICIENT_DATA",
                "reason": "no red-count rounds recorded yet", "exit_code": 0}
    if red_counts[-1] == 0:
        return {"verdict": "GREEN",
                "reason": "latest round has 0 red — nothing to stall on",
                "exit_code": 0}
    best = red_counts[0]
    last_low_idx = 0
    for i, value in enumerate(red_counts):
        if value < best:
            best = value
            last_low_idx = i
    rounds_since = (n - 1) - last_low_idx
    if n <= patience:
        return {"verdict": "INSUFFICIENT_DATA",
                "reason": (f"only {n} measured round(s); need more than {patience} "
                           f"to judge a {patience}-round stall"),
                "exit_code": 0}
    if rounds_since >= patience:
        return {"verdict": "STALL",
                "reason": (f"no new low for {rounds_since} round(s) "
                           f"(best={best}, last improved {rounds_since} rounds ago; "
                           f"patience={patience}) — stop and rethink, don't keep grinding"),
                "exit_code": 1}
    return {"verdict": "PROGRESSING",
            "reason": (f"last improvement {rounds_since} round(s) ago "
                       f"(best={best}; under patience={patience})"),
            "exit_code": 0}


def load(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Ledger does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # D3: a corrupted/torn ledger is an infra block, not a normal red. Exit 2 (BLOCKED),
        # NOT a bare-traceback exit 1 — exit 1 is stall-check's STALL verdict, so an outer
        # loop must never confuse 'ledger unreadable' with 'no progress'. Message is actionable.
        print(f"BLOCKED: ledger is corrupted and cannot be parsed: {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    # D1: atomic write — write to a temp sibling, then atomically rename over the target.
    # A crash mid-write must not truncate/corrupt an existing ledger (a torn ledger then
    # trips the stop-hook fail-open, A1). Mirrors rgm_gate.py's --write-ledger atomicity.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def cmd_init(args: argparse.Namespace) -> None:
    path = Path(args.out)
    if path.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing ledger: {path} (use --force)")
    if args.mode not in VALID_MODES:
        raise SystemExit(f"Invalid mode {args.mode!r}; expected one of {sorted(VALID_MODES)}")
    data = {
        "task": args.task,
        "workspace": args.workspace,
        "skill_version": args.skill_version,
        "started_at": now_iso(),
        "verification_commands": args.verify or [],
        "runtime": args.runtime or "",
        "initial_status": args.initial_status or "",
        "mode": args.mode,
        "window": args.window,
        "budget": {},
        "context_checkpoints": [],
        "entries": [],
        "final": {},
    }
    save(path, data)
    print(f"initialized {path} (mode={args.mode})")


def cmd_add(args: argparse.Namespace) -> None:
    path = Path(args.out)
    data = load(path)
    if args.state not in VALID_STATES:
        raise SystemExit(f"Invalid state {args.state!r}; expected one of {sorted(VALID_STATES)}")
    if args.label not in VALID_LABELS:
        raise SystemExit(f"Invalid label {args.label!r}; expected one of {sorted(VALID_LABELS)}")
    entry = {
        "round": args.round if args.round is not None else len(data.get("entries", [])),
        "timestamp": now_iso(),
        "state": args.state,
        "command": args.command or "",
        "label": args.label or "",
        "hypothesis": args.hypothesis or "",
        "change": args.change or "",
        "result": args.result or "",
        # Numeric count of still-failing checks this round (None when not a
        # measured verify round). This is what `stall-check` computes the
        # progress trend on — free-text `result` stays human-only.
        "red_count": args.red_count,
        "next": args.next or "",
    }
    data.setdefault("entries", []).append(entry)
    save(path, data)
    print(f"added round {entry['round']} {entry['state']}")


def cmd_finish(args: argparse.Namespace) -> None:
    path = Path(args.out)
    if args.completion_state not in VALID_COMPLETION_STATES:
        raise SystemExit(
            f"Invalid completion-state {args.completion_state!r}; "
            f"expected one of {sorted(VALID_COMPLETION_STATES)}"
        )
    data = load(path)
    data["final"] = {
        "completed_at": now_iso(),
        "completion_state": args.completion_state,
        "before_after": args.before_after or "",
        "changed_files": args.changed_files or [],
        "semantic_smoke": args.semantic_smoke or "",
        "quality_review": args.quality_review or "",
        "quality_score": args.quality_score or "",
        "checkpoint_summary": args.checkpoint_summary or "",
        "remaining_risks": args.remaining_risks or "",
        "prd_coverage": args.prd_coverage or "",
    }
    save(path, data)
    print(f"finished {path} as {args.completion_state}")


def md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("|", "\\|")


def cmd_report(args: argparse.Namespace) -> None:
    data = load(Path(args.out))
    lines = []
    lines.append("# Red-Green Run Ledger Report")
    lines.append("")
    lines.append(f"- Task: {data.get('task','')}")
    lines.append(f"- Workspace: {data.get('workspace','')}")
    lines.append(f"- Skill version: {data.get('skill_version','')}")
    lines.append(f"- Started at: {data.get('started_at','')}")
    if data.get("runtime"):
        lines.append(f"- Runtime: {data['runtime']}")
    if data.get("initial_status"):
        lines.append(f"- Initial status: {data['initial_status']}")
    lines.append("")
    lines.append("| Round | State | Command | Label | Hypothesis | Change | Result | Next |")
    lines.append("|------:|-------|---------|-------|------------|--------|--------|------|")
    for e in data.get("entries", []):
        lines.append(
            "| {round} | {state} | {command} | {label} | {hypothesis} | {change} | {result} | {next} |".format(
                round=md_escape(e.get("round")),
                state=md_escape(e.get("state")),
                command=md_escape(e.get("command")),
                label=md_escape(e.get("label")),
                hypothesis=md_escape(e.get("hypothesis")),
                change=md_escape(e.get("change")),
                result=md_escape(e.get("result")),
                next=md_escape(e.get("next")),
            )
        )
    final = data.get("final") or {}
    if final:
        lines.append("")
        lines.append("## Final")
        duration = compute_duration(data)
        lines.append(f"- Duration: {duration if duration else 'n/a'}")
        lines.append(f"- Completed at: {final.get('completed_at', '')}")
        for key in ["completion_state", "before_after", "changed_files", "semantic_smoke", "quality_review", "quality_score", "prd_coverage", "checkpoint_summary", "remaining_risks"]:
            value = final.get(key)
            if isinstance(value, list):
                value = ", ".join(value)
            lines.append(f"- {key}: {value or ''}")
    print("\n".join(lines))


def cmd_budget(args: argparse.Namespace) -> None:
    if args.zone not in VALID_ZONES:
        raise SystemExit(f"Invalid zone {args.zone!r}; expected one of {sorted(VALID_ZONES)}")
    path = Path(args.out)
    data = load(path)
    data["budget"] = {
        "round": args.round,
        "ingested_tokens": args.ingested,
        "zone": args.zone,
    }
    save(path, data)
    print(f"budget round={args.round} ingested={args.ingested} zone={args.zone}")


def cmd_checkpoint(args: argparse.Namespace) -> None:
    path = Path(args.out)
    data = load(path)
    cp = {
        "at_round": args.at_round,
        "reason": args.reason or "",
        "handoff": args.handoff or "",
        "timestamp": now_iso(),
        "resumed": False,  # flipped to True by `resume` once cold-start succeeds
    }
    data.setdefault("context_checkpoints", []).append(cp)
    save(path, data)
    print(f"context checkpoint at round {args.at_round}")


def cmd_resume(args: argparse.Namespace) -> None:
    path = Path(args.out)
    data = load(path)
    cps = data.get("context_checkpoints") or []
    if not cps:
        raise SystemExit("Cannot resume: no context checkpoint recorded")
    cps[-1]["resumed"] = True
    cps[-1]["resumed_at"] = now_iso()
    save(path, data)
    print("marked latest context checkpoint resumed")


def cmd_bootstrap(args: argparse.Namespace) -> None:
    """Print everything a restarted session needs to resume from files alone."""
    data = load(Path(args.out))
    entries = data.get("entries", [])
    cursor = ""
    for e in reversed(entries):
        if e.get("next"):
            cursor = e["next"]
            break
    if not cursor:
        cursor = entries[-1]["state"] if entries else "PRECHECK"
    decisions = [e["hypothesis"] for e in entries if e.get("hypothesis")][-3:]
    budget = data.get("budget") or {}
    cps = data.get("context_checkpoints") or []
    all_resumed = all(c.get("resumed") for c in cps) if cps else True

    lines = ["# Cold-start bootstrap"]
    lines.append(f"- Task: {data.get('task', '')}")
    lines.append(f"- Mode: {data.get('mode', 'single')}")
    lines.append(f"- Window: {data.get('window', 0)}")
    lines.append(f"- Cursor (resume from): {cursor}")
    if budget:
        lines.append(
            f"- Budget: zone={budget.get('zone', '')} round={budget.get('round', '')} "
            f"ingested={budget.get('ingested_tokens', '')}"
        )
    lines.append(f"- Context checkpoints: {len(cps)} (all resumed: {all_resumed})")
    lines.append("- Recent decisions:")
    for d in decisions:
        lines.append(f"  - {d}")
    print("\n".join(lines))


def cmd_stall_check(args: argparse.Namespace) -> None:
    """Verdict on whether the run is stalling; exits 1 on STALL so a shell loop
    or agent can branch on it (the stop decision is external, not eyeballed)."""
    data = load(Path(args.out))
    reds = [
        e["red_count"]
        for e in data.get("entries", [])
        if isinstance(e.get("red_count"), int) and not isinstance(e.get("red_count"), bool)
    ]
    result = evaluate_stall(reds, patience=args.patience)
    print(f"{result['verdict']}: {result['reason']}")
    raise SystemExit(result["exit_code"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Ledger helper for red-green-mode")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init", help="create a ledger JSON file")
    init.add_argument("--out", required=True)
    init.add_argument("--task", required=True)
    init.add_argument("--workspace", default="")
    init.add_argument("--skill-version", default="red-green-mode")
    init.add_argument("--verify", action="append", help="verification command; repeatable")
    init.add_argument("--runtime", default="")
    init.add_argument("--initial-status", default="")
    init.add_argument("--mode", default="single", help="single | longrun | parallel")
    init.add_argument("--window", type=int, default=0, help="context window in tokens (longrun)")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    add = sub.add_parser("add", help="append a ledger entry")
    add.add_argument("--out", required=True)
    add.add_argument("--round", type=int)
    add.add_argument("--state", required=True)
    add.add_argument("--command", default="")
    add.add_argument("--label", default="")
    add.add_argument("--hypothesis", default="")
    add.add_argument("--change", default="")
    add.add_argument("--result", default="")
    add.add_argument("--red-count", type=int, default=None,
                     help="number of still-failing checks this round (feeds stall-check)")
    add.add_argument("--next", default="")
    add.set_defaults(func=cmd_add)

    finish = sub.add_parser("finish", help="write final report fields")
    finish.add_argument("--out", required=True)
    finish.add_argument("--completion-state", required=True)
    finish.add_argument("--before-after", default="")
    finish.add_argument("--changed-files", action="append")
    finish.add_argument("--semantic-smoke", default="")
    finish.add_argument("--quality-review", default="")
    finish.add_argument("--quality-score", default="")
    finish.add_argument("--checkpoint-summary", default="")
    finish.add_argument("--remaining-risks", default="")
    finish.add_argument("--prd-coverage", default="",
                        help="PRD acceptance-item coverage summary (done/uncovered/missing)")
    finish.set_defaults(func=cmd_finish)

    budget = sub.add_parser("budget", help="update window/progress budget block (longrun)")
    budget.add_argument("--out", required=True)
    budget.add_argument("--round", type=int, default=0)
    budget.add_argument("--ingested", type=int, default=0)
    budget.add_argument("--zone", required=True, help="green | amber | red")
    budget.set_defaults(func=cmd_budget)

    checkpoint = sub.add_parser("checkpoint", help="record a context checkpoint (longrun)")
    checkpoint.add_argument("--out", required=True)
    checkpoint.add_argument("--at-round", type=int, required=True)
    checkpoint.add_argument("--reason", default="")
    checkpoint.add_argument("--handoff", default="")
    checkpoint.set_defaults(func=cmd_checkpoint)

    resume = sub.add_parser("resume", help="mark latest context checkpoint resumed (post cold-start)")
    resume.add_argument("--out", required=True)
    resume.set_defaults(func=cmd_resume)

    bootstrap = sub.add_parser("bootstrap", help="print cold-start summary for a restarted session")
    bootstrap.add_argument("--out", required=True)
    bootstrap.set_defaults(func=cmd_bootstrap)

    stall = sub.add_parser(
        "stall-check",
        help="exit 1 if the run is stalling (no new low red-count in N rounds)",
    )
    stall.add_argument("--out", required=True)
    stall.add_argument("--patience", type=int, default=5,
                       help="rounds with no new lowest red-count before declaring "
                            "STALL (default 5; the primary anti-spin judge)")
    stall.set_defaults(func=cmd_stall_check)

    report = sub.add_parser("report", help="print markdown report")
    report.add_argument("--out", required=True)
    report.set_defaults(func=cmd_report)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

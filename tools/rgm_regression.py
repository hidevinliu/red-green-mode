#!/usr/bin/env python3
"""Lightweight regression helper for red-green-mode eval metadata/results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED_EVAL_FIELDS = {"id", "prompt", "expected_output", "guardrail"}
REQUIRED_RESULT_FIELDS = {"id", "status", "score"}


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cmd_validate(args: argparse.Namespace) -> None:
    data = load_json(args.evals)
    if data.get("skill_name") != "red-green-mode":
        raise SystemExit("Expected skill_name=red-green-mode")
    ids = set()
    errors = []
    for i, ev in enumerate(data.get("evals", [])):
        missing = REQUIRED_EVAL_FIELDS - set(ev)
        if missing:
            errors.append(f"eval[{i}] missing {sorted(missing)}")
        if ev.get("id") in ids:
            errors.append(f"duplicate id {ev.get('id')}")
        ids.add(ev.get("id"))
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"valid evals={len(ids)} version={data.get('version')}")


def cmd_checklist(args: argparse.Namespace) -> None:
    data = load_json(args.evals)
    print(f"# red-green-mode regression checklist ({data.get('version')})")
    for ev in data.get("evals", []):
        print(f"- [ ] {ev['id']} — {ev['guardrail']}")
        print(f"      expect: {ev['expected_output']}")


def cmd_score(args: argparse.Namespace) -> None:
    data = load_json(args.results)
    results = data.get("results", [])
    errors = []
    total = 0.0
    passed = 0
    for i, item in enumerate(results):
        missing = REQUIRED_RESULT_FIELDS - set(item)
        if missing:
            errors.append(f"result[{i}] missing {sorted(missing)}")
            continue
        try:
            score = float(item["score"])
        except (TypeError, ValueError):
            errors.append(f"result[{i}] score not numeric: {item['score']!r}")
            continue
        if not 0 <= score <= 10:
            errors.append(f"result[{i}] score out of range: {score}")
        total += score
        if item.get("status") == "pass":
            passed += 1
    if errors:
        raise SystemExit("\n".join(errors))
    count = len(results)
    avg = total / count if count else 0.0
    print(f"results={count} passed={passed} avg_score={avg:.2f}")
    if count and avg < args.min_avg:
        raise SystemExit(f"average score {avg:.2f} below threshold {args.min_avg}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Regression helper for red-green-mode")
    sub = parser.add_subparsers(dest="cmd", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--evals", required=True)
    validate.set_defaults(func=cmd_validate)
    checklist = sub.add_parser("checklist")
    checklist.add_argument("--evals", required=True)
    checklist.set_defaults(func=cmd_checklist)
    score = sub.add_parser("score")
    score.add_argument("--results", required=True)
    score.add_argument("--min-avg", type=float, default=8.0)
    score.set_defaults(func=cmd_score)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

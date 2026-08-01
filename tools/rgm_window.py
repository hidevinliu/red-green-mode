#!/usr/bin/env python3
"""Window watchdog for red-green-mode long-run mode.

Most runtimes can't read their own exact context-window % from inside the
process, so this estimates pressure from observable proxies — cumulative
ingested tokens (chars // 4) plus the loop round count — and reports a zone:

  green : below soft thresholds, keep going
  amber : draining — finish the current sub-task, don't open new big work
  red   : checkpoint now — write the hand-off and restart / cold-start

Dual trigger: a zone fires when EITHER the round count OR the ingested-token
count crosses its threshold, so a burst of big tool output trips it even at a
low round, and a long slow run trips it even with small outputs.

Usage:
  python tools/rgm_window.py estimate --chars 524288
  python tools/rgm_window.py zone --runtime claude-code --round 22 --ingested 131000
  python tools/rgm_window.py zone --window 200000 --round 25 --ingested 1 --exit-code
"""
from __future__ import annotations

import argparse
import json

# Context window sizes per runtime (see the context-governance design note).
RUNTIME_WINDOWS = {
    "claude-code": 1_000_000,
    "codex": 200_000,
}

ZONE_EXIT_CODES = {"green": 0, "amber": 10, "red": 20}


def estimate_tokens(chars: int) -> int:
    """Proxy for tokens entering the main context: roughly chars / 4."""
    return chars // 4


def classify(
    window: int,
    round_: int,
    ingested: int,
    soft_round: int,
    hard_round: int,
    soft_frac: float,
    hard_frac: float,
) -> dict:
    # Round to integer tokens: 0.55*200000 is 110000.00000000001 in float,
    # which would wrongly read the exact 55% boundary as below-threshold.
    soft_tok = round(soft_frac * window)
    hard_tok = round(hard_frac * window)

    if round_ >= hard_round or ingested >= hard_tok:
        zone = "red"
        triggers = []
        if round_ >= hard_round:
            triggers.append(f"round>={hard_round}")
        if ingested >= hard_tok:
            triggers.append(f"ingested>={hard_frac}*window")
        reason = "; ".join(triggers)
    elif round_ >= soft_round or ingested >= soft_tok:
        zone = "amber"
        triggers = []
        if round_ >= soft_round:
            triggers.append(f"round>={soft_round}")
        if ingested >= soft_tok:
            triggers.append(f"ingested>={soft_frac}*window")
        reason = "; ".join(triggers)
    else:
        zone = "green"
        reason = "below soft thresholds"

    return {
        "zone": zone,
        "checkpoint": zone == "red",
        "window": window,
        "round": round_,
        "ingested": ingested,
        "soft_tokens": int(soft_tok),
        "hard_tokens": int(hard_tok),
        "reason": reason,
    }


def cmd_estimate(args: argparse.Namespace) -> None:
    print(estimate_tokens(args.chars))


def cmd_zone(args: argparse.Namespace) -> None:
    if args.runtime:
        if args.runtime not in RUNTIME_WINDOWS:
            raise SystemExit(
                f"Unknown runtime {args.runtime!r}; "
                f"expected one of {sorted(RUNTIME_WINDOWS)}"
            )
        window = RUNTIME_WINDOWS[args.runtime]
    elif args.window:
        window = args.window
    else:
        raise SystemExit("Must provide --window or --runtime")

    result = classify(
        window=window,
        round_=args.round,
        ingested=args.ingested,
        soft_round=args.soft_round,
        hard_round=args.hard_round,
        soft_frac=args.soft_frac,
        hard_frac=args.hard_frac,
    )
    print(json.dumps(result, ensure_ascii=False))
    if args.exit_code:
        raise SystemExit(ZONE_EXIT_CODES[result["zone"]])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Window watchdog for red-green-mode long-run mode")
    sub = parser.add_subparsers(dest="cmd", required=True)

    est = sub.add_parser("estimate", help="proxy ingested tokens from a char count (chars // 4)")
    est.add_argument("--chars", type=int, required=True)
    est.set_defaults(func=cmd_estimate)

    zone = sub.add_parser("zone", help="classify context pressure into green/amber/red")
    zone.add_argument("--window", type=int, default=0, help="context window size in tokens")
    zone.add_argument("--runtime", default="", help="claude-code | codex (sets --window)")
    zone.add_argument("--round", type=int, default=0, help="current loop round count")
    zone.add_argument("--ingested", type=int, default=0, help="approx tokens ingested into main context")
    zone.add_argument("--soft-round", type=int, default=18)
    zone.add_argument("--hard-round", type=int, default=25)
    zone.add_argument("--soft-frac", type=float, default=0.55)
    zone.add_argument("--hard-frac", type=float, default=0.70)
    zone.add_argument("--exit-code", action="store_true",
                      help="exit green=0/amber=10/red=20 so a shell loop can branch")
    zone.set_defaults(func=cmd_zone)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

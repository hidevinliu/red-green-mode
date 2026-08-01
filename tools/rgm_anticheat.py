#!/usr/bin/env python3
"""Anti-cheat verifier for red-green-mode (v0.10.0 mechanical enforcement layer, the exit-code
judge for Ironclad Rule 1).

Ironclad Rule 1, "never cheat your way to green", had always been a prompt-level prohibition with
no verifier behind it. This tool supplies one: it scans a unified git diff and catches the cheating
moves *newly introduced* by this run (skipping failing tests / weakening assertions / deleting
assertions / papering over static checks / mocking the logic under test), and rules on them with an
exit code — 0 = clean, 1 = cheating found, 2 = could not run.

It looks only at the diff's added/removed lines and never touches pre-existing code (a
`# type: ignore` that was already there is not cheating introduced by this run).
Escape hatch: a line carrying `rgm-allow: <reason>` is downgraded to allowed (a legitimate case).
The reason is included in the JSON output so the caller (rgm_gate / rgm_ledger) can record it in
the ledger — the only thing separating cheating from legitimate work is whether there is an
auditable reason.

Usage:
  python3 tools/rgm_anticheat.py scan --diff-file <path> [--format json|sentinel] [--paths p1 p2]

The 8 detection categories:
  1 Python test skips        2 JS/TS test skips       3 Go/Rust skips
  4 static-check suppression 5 trivially-true asserts 6 deleted assertions/tests
  7 mocking the logic under test (warn)               8 loosened judge strictness
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# (category, severity, compiled-regex). severity: "fail" contributes to exit code 1; "warn" is reported but does not fail.
ADD_RULES: list[tuple[int, str, re.Pattern[str]]] = [
    (1, "fail", re.compile(r"@pytest\.mark\.(skip|skipif|xfail)\b|(?<![\w.])pytest\.(skip|xfail)\s*\(|@unittest\.skip|self\.skipTest\s*\(|raise\s+[\w.]*SkipTest|(?<![\w.])SkipTest\s*\(")),
    (2, "fail", re.compile(r"\.only\s*\(|\.skip\s*\(|(?<![\w.])xit\s*\(|(?<![\w.])xdescribe\s*\(|(?<![\w.])test\.skip\b|(?<![\w.])describe\.skip\b")),
    (3, "fail", re.compile(r"(?<![\w.])t\.Skip\w*\s*\(|#\[ignore\]")),
    # B1: IGNORECASE — ruff/flake8 treat `# NOQA` == `# noqa`; the lowercase-only regex
    # let an uppercased suppression walk past. All alternatives here are casing-invariant
    # suppressions that should be caught in any case.
    (4, "fail", re.compile(r"#\s*type:\s*ignore|#\s*noqa|@ts-ignore|eslint-disable|#\[allow\(", re.IGNORECASE)),
    # cat5 trivially-true asserts: literal True/1, assertTrue(True), and tautologies
    # where both sides of == are the *same* token (assert x == x / assert 0 == 0).
    # B2 added: `assert(True)` (parenthesized) and `assert N` (nonzero literal) anchored to
    # end/comma so `assert 200 == x` (a real comparison) is NOT flagged; the nonzero alt
    # subsumes the old bare `assert 1` and drops its false positive on `assert 1 == x`.
    (5, "fail", re.compile(r"\bassert\s+True\b|\bassert\s*\(\s*True\s*\)|\bassert\s+[1-9]\d*\s*(?:,|#|$)|assertTrue\s*\(\s*True\s*\)|expect\s*\(\s*true\s*\)\s*\.toBe\s*\(\s*true\s*\)|\bassert\s+(\w+)\s*==\s*\1\b")),
    (7, "warn", re.compile(r"\bmock\.patch\b|\bMagicMock\b|(?<![\w.])patch\s*\(|jest\.mock\s*\(")),
    (8, "fail", re.compile(r"per-file-ignores|disable-next-line|\"rules\"\s*:\s*\{\s*\}|noImplicitAny\"?\s*:\s*false")),
    # cat9 (warn): can't detect semantic hardcoding, but an explicit "hardcode/to pass
    # the test" hint in production code is a smell worth surfacing for human review.
    (9, "warn", re.compile(r"#.*(hardcode|to\s+pass\s+the\s+test|just\s+to\s+pass|fool\s+the\s+test|骗过|蒙混|写死.*过)")),
]
# removed-line rules (category 6): deleting an assertion or a test function.
DEL_RULES: list[tuple[int, str, re.Pattern[str]]] = [
    (6, "fail", re.compile(r"(?<!\w)assert\w*\s*\(|^\s*assert\b|^\s*def\s+test_|^\s*(it|test)\s*\(|^\s*expect\s*\(")),
]
# reason must START with a non-space char — a whitespace-only `# rgm-allow:   ` is
# NOT an auditable reason, so it must NOT downgrade the finding (code review P1-4).
ALLOW_RE = re.compile(r"rgm-allow:\s*(?P<reason>\S.*?)\s*$")


def _in_string(content: str, pos: int) -> bool:
    """Is the match at `pos` a *mention* (string literal / markdown code span) rather
    than real code? A proper char-walk, NOT a naive quote-count.

    Why a state machine: test files for an anti-cheat tool legitimately carry cheat
    patterns as data (`diff = "+ @pytest.mark.skip"`) — those must downgrade to warn.
    But a naive quote-parity counter is worse than useless: an apostrophe in a closed
    string (`"user's data"  # type: ignore`) or in comment prose (`# don't  # noqa`)
    flips parity and would HIDE a real suppression — the exact opposite of Ironclad Rule 1.

    Rules: track open string (", ' with backslash-escape) and backtick span; a `#`
    outside a string starts a comment where quotes are prose (ignored) but backtick
    code-spans still count. A pattern is a mention iff it sits inside an open
    string OR an open backtick span at `pos`.
    """
    in_str: str | None = None
    in_tick = False
    in_comment = False
    i = 0
    while i < pos and i < len(content):
        c = content[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif in_tick:
            if c == "`":
                in_tick = False
        elif in_comment:
            if c == "`":          # markdown code span inside a comment still counts
                in_tick = True
        else:
            if c == "`":
                in_tick = True
            elif c in ('"', "'"):
                in_str = c
            elif c == "#":
                in_comment = True
        i += 1
    return in_str is not None or in_tick


def _parse_diff(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (findings, allows). Only added/removed lines are inspected."""
    findings: list[dict[str, Any]] = []
    allows: list[dict[str, Any]] = []
    cur_file = "?"
    for raw in text.splitlines():
        if raw.startswith("+++"):
            cur_file = re.sub(r"^\+\+\+\s+b/", "", raw).strip() or cur_file
            continue
        if raw.startswith("---") or raw.startswith("diff ") or raw.startswith("@@") or raw.startswith("index "):
            continue
        if raw.startswith("+"):
            content = raw[1:]
            rules, line_kind = ADD_RULES, "added"
        elif raw.startswith("-"):
            content = raw[1:]
            rules, line_kind = DEL_RULES, "removed"
        else:
            continue  # context line — never a finding

        allow = ALLOW_RE.search(content)
        for cat, sev, rx in rules:
            m = rx.search(content)
            if not m:
                continue
            if allow:
                allows.append({"file": cur_file, "category": cat, "kind": line_kind,
                               "snippet": content.strip()[:160], "reason": allow.group("reason")})
                continue
            in_str = _in_string(content, m.start())
            findings.append({"file": cur_file, "category": cat,
                             "severity": "warn" if in_str else sev,
                             "kind": line_kind, "in_string": in_str,
                             "snippet": content.strip()[:160]})
    return findings, allows


def cmd_scan(args: argparse.Namespace) -> int:
    diff_path = Path(args.diff_file)
    if not diff_path.exists():
        print(f"BLOCKED: diff file not found: {diff_path}", file=sys.stderr)
        return 2
    findings, allows = _parse_diff(diff_path.read_text(encoding="utf-8"))

    if args.paths:
        scope = [p.rstrip("/") for p in args.paths]
        keep = lambda f: any(f["file"] == p or f["file"].startswith(p + "/") for p in scope)
        findings = [f for f in findings if keep(f)]
        allows = [a for a in allows if keep(a)]

    fails = [f for f in findings if f["severity"] == "fail"]
    clean = not fails
    warns = [f for f in findings if f["severity"] == "warn"]

    if args.format == "sentinel":
        print("<!--RGM_ANTICHEAT_BEGIN-->")
        print(f"ANTICHEAT={'PASS' if clean else 'FAIL'}")
        print(f"FINDINGS={len(fails)}")
        print(f"WARNINGS={len(warns)}")
        print(f"ALLOWS={len(allows)}")
        print("<!--RGM_ANTICHEAT_END-->")
    else:
        print(json.dumps({"clean": clean, "findings": findings, "allows": allows},
                         ensure_ascii=False))
    return 0 if clean else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Anti-cheat verifier for red-green-mode")
    sub = parser.add_subparsers(dest="cmd", required=True)
    scan = sub.add_parser("scan", help="scan a unified diff for newly-introduced cheating; exit 1 if found")
    scan.add_argument("--diff-file", required=True, help="path to a file containing a unified git diff")
    scan.add_argument("--format", choices=["json", "sentinel"], default="json")
    scan.add_argument("--paths", nargs="*", default=None, help="optional path scope filter")
    scan.set_defaults(func=cmd_scan)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

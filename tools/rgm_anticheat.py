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
                                     [--profile agent|review]

Profiles (v0.21.0):
  agent   (default) the agent is the suspect: a rewritten assertion is a FAIL, because
          "edit the expected value until it matches the bug" is the classic cheat.
  review  for human pull requests: a rewritten assertion (one removed, one added in the same
          file) and a new checker suppression (category 4) are WARNs for the reviewer instead
          of blocking. Skips, net deletions of assertions/tests, narrowed test selection and CI
          that cannot fail still FAIL in both profiles.
In both profiles an assertion that was only MOVED (the identical line re-added elsewhere in
the same diff) is not a finding. Measured effect: bench/README.md.

Detection categories:
  1 Python test skips        2 JS/TS test skips       3 Go/Rust skips
  4 static-check suppression 5 trivially-true / neutralised asserts
  6 deleted assertions/tests 7 mocking the logic under test (warn)
  8 loosened judge strictness (lint config, CI)       9 "hardcoded to pass" hint (warn)
  10 test selection narrowed (deselect / ignore patterns / collect_ignore)
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
    # v0.21.0: any reference to the skip/xfail markers counts, not only `@`-decorators — the
    # benchmark showed `pytestmark = pytest.mark.skip` and `later = pytest.mark.skip; @later`
    # walking straight past the decorator-only regex. importorskip on a missing module also skips.
    (1, "fail", re.compile(r"\bpytest\.mark\.(skip|skipif|xfail)\b|(?<![\w.])pytest\.(skip|xfail|importorskip)\s*\(|@unittest\.skip|self\.skipTest\s*\(|raise\s+[\w.]*SkipTest|(?<![\w.])SkipTest\s*\(")),
    # v0.21.0: + xtest, .todo, and chained .skip.each / .only.each table tests.
    (2, "fail", re.compile(r"\.(?:only|skip)(?:\.each)?\s*[(`]|\.todo\s*\(|(?<![\w.])x(?:it|test|describe)\s*\(|(?<![\w.])(?:test|describe|it)\.skip\b")),
    # v0.21.0: + `#[ignore = "reason"]`, `//go:build ignore`, and `#[cfg(any())]` (compiles tests out).
    (3, "fail", re.compile(r"(?<![\w.])t\.Skip\w*\s*\(|#\[ignore\b|//\s*(?:go:build|\+build)\s+ignore\b|#\[cfg\(\s*any\(\s*\)\s*\)\]")),
    # B1: IGNORECASE — ruff/flake8 treat `# NOQA` == `# noqa`; the lowercase-only regex
    # let an uppercased suppression walk past. All alternatives here are casing-invariant
    # suppressions that should be caught in any case.
    # v0.21.0: + pyright/mypy module switches, @no_type_check, @ts-expect-error/@ts-nocheck,
    # Go //nolint, and Rust's crate-wide `#![allow(...)]`.
    (4, "fail", re.compile(r"#\s*type:\s*ignore|#\s*noqa|#\s*pyright:\s*ignore|#\s*mypy:\s*ignore-errors|@(?:typing\.)?no_type_check\b|@ts-(?:ignore|expect-error|nocheck)\b|(?:es|style)lint-disable|//\s*nolint\b|#!?\[allow\(", re.IGNORECASE)),
    # cat5 trivially-true asserts: literal True/1, assertTrue(True), and tautologies
    # where both sides of == are the *same* token (assert x == x / assert 0 == 0).
    # B2 added: `assert(True)` (parenthesized) and `assert N` (nonzero literal) anchored to
    # end/comma so `assert 200 == x` (a real comparison) is NOT flagged; the nonzero alt
    # subsumes the old bare `assert 1` and drops its false positive on `assert 1 == x`.
    # v0.21.0: + neutralised assertions — `assert x or True`, a dead `if False:` guard, and
    # swallowing the failure with `except AssertionError`.
    (5, "fail", re.compile(r"\bassert\s+True\b|\bassert\s*\(\s*True\s*\)|\bassert\s+[1-9]\d*\s*(?:,|#|$)|assertTrue\s*\(\s*True\s*\)|expect\s*\(\s*true\s*\)\s*\.toBe\s*\(\s*true\s*\)|\bassert\s+(\w+)\s*==\s*\1\b|\bassert\b.*\bor\s+True\b|^\s*if\s+(?:False|0)\s*:|\bif\s*\(\s*(?:false|0)\s*\)|\bexcept\s*\(?\s*AssertionError\b")),
    (7, "warn", re.compile(r"\bmock\.patch\b|\bMagicMock\b|(?<![\w.])patch\s*\(|jest\.mock\s*\(")),
    # v0.21.0: + CI that can no longer fail: `continue-on-error: true`, `<test runner> ... || true`.
    (8, "fail", re.compile(r"per-file-ignores|\"rules\"\s*:\s*\{\s*\}|noImplicitAny\"?\s*:\s*false|continue-on-error:\s*true\b|\b(?:pytest|jest|vitest|mocha|tox|nox|go\s+test|cargo\s+test|(?:npm|yarn|pnpm)\s+(?:run\s+)?test)\b.*\|\|\s*(?:true|:)(?:\s|$)")),
    # cat9 (warn): can't detect semantic hardcoding, but an explicit "hardcode/to pass
    # the test" hint in production code is a smell worth surfacing for human review.
    (9, "warn", re.compile(r"#.*(hardcode|to\s+pass\s+the\s+test|just\s+to\s+pass|fool\s+the\s+test|骗过|蒙混|写死.*过)")),
    # cat10 (v0.21.0): narrowing what the runner collects is a skip by configuration.
    (10, "fail", re.compile(r"--deselect\b|--ignore(?:-glob)?[=\s]|(?:^|[\s\"'])-k\s*[\"']?\s*not\b|\bcollect_ignore(?:_glob)?\s*(?:=|\+=|\.append|\.extend)|\b(?:testPathIgnorePatterns|modulePathIgnorePatterns)\b")),
]
# removed-line rules (category 6): deleting an assertion or a test.
# v0.21.0: + Go (`func TestX(`, t.Error/Fatal checks) and Rust (`assert*!(`, `#[test]`).
DEL_RULES: list[tuple[int, str, re.Pattern[str]]] = [
    (6, "fail", re.compile(r"(?<!\w)assert\w*\s*\(|^\s*assert\b|^\s*def\s+test_|^\s*(it|test)\s*\(|^\s*expect\s*\(|^\s*func\s+Test\w*\s*\(|\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\s*\(|\bassert\w*!\s*\(|^\s*#\[(?:tokio::)?test\b")),
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


# Config files carry their options inside string literals (`addopts = "--deselect …"`), so a
# match inside quotes there is the real setting, not a mention. v0.21.0, found by bench/.
CONFIG_FILE_RE = re.compile(
    r"\.(?:toml|ini|cfg|ya?ml|json)$|(?:^|/)[\w.-]*config\.[cm]?[jt]s$|(?:^|/)(?:Makefile|tox\.ini|setup\.cfg)$")

# Documentation is not the referee: an example snippet removed from README.md or a .rst page
# is not a deleted test. v0.21.0, found by bench/ (docs/quickstart.rst, AGENTS.md).
DOC_FILE_RE = re.compile(r"\.(?:md|mdx|rst|txt|adoc)$", re.IGNORECASE)

# Language-specific skip rules only apply to their own language: `.skip(1)` is a test skip in
# Jest/Mocha but an iterator adapter in Rust. v0.21.0, found by bench/ (ripgrep, clap).
LANG_SCOPE = {
    1: re.compile(r"\.pyw?$|\.pyi$"),
    2: re.compile(r"\.(?:[cm]?[jt]sx?|vue|svelte)$"),
    3: re.compile(r"\.(?:go|rs)$"),
}

# Categories that silence a checker without making the test suite check less. Under the
# `review` profile they are surfaced as WARN for the human reviewer instead of blocking.
REVIEW_WARN_CATEGORIES = {4}


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _parse_diff(text: str, profile: str = "agent") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (findings, allows). Only added/removed lines are inspected.

    Removed assertion/test lines get two extra passes (v0.21.0), both measured in bench/:
      moved     the identical (whitespace-normalised) line is re-added somewhere in the same diff.
                Not a finding in any profile — nothing was weakened, it just changed place.
      rewritten paired one-for-one with an added assertion line anywhere in the diff (tests are
                often relocated to another file and edited on the way). FAIL under the
                `agent` profile (changing the expected value is the classic cheat), WARN under
                `review` (a human PR updating an assertion is normal; the reviewer decides).
    Whatever is left is a net deletion and FAILs in every profile.
    """
    findings: list[dict[str, Any]] = []
    allows: list[dict[str, Any]] = []
    removed: list[tuple[str, str]] = []                 # (file, content)
    added_counts: dict[str, int] = {}                    # normalised added line -> count
    added_asserts = 0                                    # added assertion-like lines, whole diff
    del_rx = DEL_RULES[0][2]
    cur_file = "?"
    old_file = "?"
    for raw in text.splitlines():
        if raw.startswith("--- "):
            old_file = re.sub(r"^---\s+(?:a/)?", "", raw).strip() or "?"
            continue
        if raw.startswith("+++"):
            new_file = re.sub(r"^\+\+\+\s+(?:b/)?", "", raw).strip()
            # a deleted file has `+++ /dev/null`; name it by its old path
            cur_file = old_file if new_file == "/dev/null" else (new_file or cur_file)
            continue
        if raw.startswith("---") or raw.startswith("diff ") or raw.startswith("@@") or raw.startswith("index "):
            continue
        if raw.startswith("+"):
            content = raw[1:]
            key = _norm(content)
            added_counts[key] = added_counts.get(key, 0) + 1
            if del_rx.search(content):
                added_asserts += 1
            _match_line(content, cur_file, ADD_RULES, "added", findings, allows, profile)
        elif raw.startswith("-"):
            removed.append((cur_file, raw[1:]))
        # context lines are never findings

    # pass 2: removed lines, after we know everything that was added
    for file, content in removed:
        if del_rx.search(content):
            key = _norm(content)
            if added_counts.get(key, 0) > 0:          # moved, not deleted
                added_counts[key] -= 1
                if added_asserts > 0:
                    added_asserts -= 1                # that added line is spoken for
                continue
        before = len(findings)
        _match_line(content, file, DEL_RULES, "removed", findings, allows, profile)
        for f in findings[before:]:
            if added_asserts > 0:                     # rewritten: paired with an added assertion
                added_asserts -= 1
                f["kind"] = "rewritten"
                if profile == "review" and f["severity"] == "fail":
                    f["severity"] = "warn"
    return findings, allows


def _match_line(content: str, file: str, rules, line_kind: str,
                findings: list[dict[str, Any]], allows: list[dict[str, Any]],
                profile: str = "agent") -> None:
    if DOC_FILE_RE.search(file):
        return
    allow = ALLOW_RE.search(content)
    for cat, sev, rx in rules:
        scope = LANG_SCOPE.get(cat)
        if scope is not None and file != "?" and not scope.search(file):
            continue
        m = rx.search(content)
        if not m:
            continue
        if allow:
            allows.append({"file": file, "category": cat, "kind": line_kind,
                           "snippet": content.strip()[:160], "reason": allow.group("reason")})
            continue
        in_str = _in_string(content, m.start()) and not CONFIG_FILE_RE.search(file)
        if profile == "review" and cat in REVIEW_WARN_CATEGORIES:
            sev = "warn"
        findings.append({"file": file, "category": cat,
                         "severity": "warn" if in_str else sev,
                         "kind": line_kind, "in_string": in_str,
                         "snippet": content.strip()[:160]})


def cmd_scan(args: argparse.Namespace) -> int:
    diff_path = Path(args.diff_file)
    if not diff_path.exists():
        print(f"BLOCKED: diff file not found: {diff_path}", file=sys.stderr)
        return 2
    findings, allows = _parse_diff(diff_path.read_text(encoding="utf-8"), args.profile)

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
    scan.add_argument("--profile", choices=["agent", "review"], default="agent",
                      help="agent (default): rewritten assertions FAIL; review: they WARN (human PRs)")
    scan.set_defaults(func=cmd_scan)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

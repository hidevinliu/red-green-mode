#!/usr/bin/env python3
"""Project-constraint gate for red-green-mode v0.10.0 (S4).

Derives checkable invariants from a repo's CLAUDE.md / AGENTS.md and adjudicates a
diff against the *mechanically-checkable subset*. Honest by design: only path-readonly
and protected-config rules become exit-code gates; everything else (wording / tone / framing) goes
to a human checklist and is NEVER auto-failed. We do not pretend to enforce all of CLAUDE.md.

Subcommands:
  derive  --root <repo>                    list rules (mechanical + human_checklist), with source: pointers
  check   --root <repo> --diff-file <p>    adjudicate the diff; exit 1 if a mechanical rule is violated

Exit codes: 0 = clean, 1 = violation, 2 = can't run.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DOC_NAMES = ("CLAUDE.md", "AGENTS.md")
READONLY_KW = ("永远只读", "只读不改", "只读", "read-only", "readonly",
               "绝不修改", "不可修改", "不要修改", "不准修改", "不碰", "别动",
               "never modify", "do not modify", "do not touch")
CONSTRAINT_KW = ("绝不", "禁止", "必须", "不要", "别", "不可", "never", "must", "do not", "don't")
BACKTICK_RE = re.compile(r"`([^`]+)`")
# built-in protected-config (always on, even without CLAUDE.md)
PROTECTED_RE = re.compile(r"(^|/)settings\.json$|(^|/)[\w-]*_lint\.py$")


def _norm(p: str) -> str:
    return p.strip().rstrip("/")


# bare path-shaped tokens (ASCII classes so we don't swallow CJK prose): has a slash,
# OR a dotfile/dir (.vscode), OR an NN-name (01-vendor).
BARE_PATH_RE = re.compile(
    r"[A-Za-z0-9_.\-]*/[A-Za-z0-9_.\-/]*|\.[A-Za-z][A-Za-z0-9_.\-/]*|\d+-[A-Za-z0-9_.\-/]*")


# tokens that look like URLs (a domain or a scheme) are never a readonly *path*.
URL_RE = re.compile(r"://|\b[\w.\-]+\.(?:com|org|net|io|dev|cn|gov|edu|ai|co)(?:/|$)", re.I)
# reference words: a path introduced by one of these ("see X", "参考 X") is a pointer, not the target.
REF_WORDS = ("参考", "参照", "参见", "参阅", "详见", "参看", "见", "see", "refer", "ref", "cf")


def _ok_token(tok: str) -> bool:
    return len(tok) >= 2 and any(ch.isalnum() for ch in tok) and not URL_RE.search(tok)


def _preceded_by_ref(before: str) -> bool:
    tail = before.rstrip().rstrip("：:").rstrip()
    return any(tail.endswith(w) for w in REF_WORDS)


def _path_tokens(line: str) -> list[str]:
    """Path-like tokens from a readonly line. Backtick-quoted tokens win (explicit
    intent, accepted even without a slash so `vendor`/`src` work); otherwise fall back
    to bare path-shaped tokens. Excludes URLs and paths introduced by a reference word from
    REF_WORDS ("see X" and its CJK equivalents) — those are references, not readonly targets;
    over-eager extraction would block legitimate edits (code review P1-5)."""
    bt = [t.strip() for t in BACKTICK_RE.findall(line)
          if t.strip() and " " not in t.strip() and not URL_RE.search(t.strip())]
    if bt:
        return bt
    bare = []
    for m in BARE_PATH_RE.finditer(line):
        tok = m.group(0).strip().strip("/")
        if _ok_token(tok) and not _preceded_by_ref(line[:m.start()]):
            bare.append(tok)
    return bare


def _derive(root: Path) -> dict[str, Any]:
    mechanical: list[dict[str, Any]] = []
    human: list[dict[str, Any]] = []
    seen_globs: set[str] = set()
    for name in DOC_NAMES:
        doc = root / name
        if not doc.exists():
            continue
        for lineno, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            src = f"{name}:{lineno}"
            has_readonly = any(kw in line for kw in READONLY_KW)
            if has_readonly:
                tokens = _path_tokens(line)
                if tokens:
                    for tok in tokens:
                        glob = _norm(tok)
                        if glob and glob not in seen_globs:
                            seen_globs.add(glob)
                            mechanical.append({"rule": line.strip()[:120], "kind": "readonly",
                                               "glob": glob, "source": src})
                else:
                    # readonly intent but no extractable path -> surface, don't silently swallow
                    human.append({"text": line.strip()[:160], "source": src, "note": "reads as read-only but no path could be extracted; check by hand"})
            elif any(kw in line for kw in CONSTRAINT_KW) and line.strip():
                human.append({"text": line.strip()[:160], "source": src})
    return {"mechanical": mechanical, "human_checklist": human}


def _touched_files(diff_text: str) -> list[str]:
    files: list[str] = []
    for raw in diff_text.splitlines():
        if raw.startswith("+++"):
            f = re.sub(r"^\+\+\+\s+b/", "", raw).strip()
            if f and f != "/dev/null":
                files.append(f)
    return files


def cmd_derive(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.exists():
        print(f"BLOCKED: root not found: {root}", file=sys.stderr)
        return 2
    print(json.dumps(_derive(root), ensure_ascii=False))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root)
    diff_path = Path(args.diff_file)
    if not root.exists() or not diff_path.exists():
        print("BLOCKED: root or diff file missing", file=sys.stderr)
        return 2
    rules = _derive(root)["mechanical"]
    touched = _touched_files(diff_path.read_text(encoding="utf-8"))

    violations: list[dict[str, Any]] = []
    for f in touched:
        nf = _norm(f)
        for m in rules:
            g = _norm(m["glob"])
            if g and (nf == g or nf.startswith(g + "/")):
                violations.append({"path": f, "rule": m["rule"], "kind": m["kind"], "source": m["source"]})
        if PROTECTED_RE.search(f):
            violations.append({"path": f, "rule": "protected config (settings.json / *_lint.py)", "kind": "protected",
                               "source": "builtin"})

    clean = not violations
    if args.format == "sentinel":
        print("<!--RGM_CONSTRAINTS_BEGIN-->")
        print(f"CONSTRAINTS={'PASS' if clean else 'FAIL'}")
        print(f"VIOLATIONS={len(violations)}")
        print("<!--RGM_CONSTRAINTS_END-->")
    else:
        print(json.dumps({"clean": clean, "violations": violations}, ensure_ascii=False))
    return 0 if clean else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project-constraint gate (reads CLAUDE.md/AGENTS.md)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("derive", help="list constraint rules (mechanical + human checklist)")
    d.add_argument("--root", required=True)
    d.set_defaults(func=cmd_derive)
    c = sub.add_parser("check", help="adjudicate a diff against the mechanical subset; exit 1 if violated")
    c.add_argument("--root", required=True)
    c.add_argument("--diff-file", required=True)
    c.add_argument("--format", choices=["json", "sentinel"], default="json")
    c.set_defaults(func=cmd_check)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

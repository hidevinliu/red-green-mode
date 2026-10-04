#!/usr/bin/env python3
"""Anti-cheat benchmark: how often does rgm_anticheat.py catch a cheat, and how often does it
block an honest pull request?

Two corpora, scored through the scanner's real CLI (exit codes, not internal functions):

  recall  bench/cheats.py — seeded cheats, one known technique each. Outcome per cheat:
          blocked (exit 1) / warned (exit 0 with warnings) / missed.
  noise   real merged PRs from bench/manifest/real-prs.json (cached by collect_real_prs.py).
          Every one was reviewed and merged by maintainers, so every block is a block of an
          honest change: either a false detection or a legitimate judgment call that would need
          an `rgm-allow:` reason.

Pure stdlib, offline once the PR cache exists. Without the cache the noise half is skipped and
reported as such.

Usage:
  python3 bench/run_bench.py [--profile agent|review] [--label NAME] [--save]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCANNER = ROOT / "tools" / "rgm_anticheat.py"
sys.path.insert(0, str(HERE))
from cheats import CHEATS, as_diff  # noqa: E402  (local module next to this script)
from collect_real_prs import MANIFEST, cache_path  # noqa: E402


def scanner_supports_profile() -> bool:
    out = subprocess.run([sys.executable, str(SCANNER), "scan", "--help"], capture_output=True, text=True)
    return "--profile" in out.stdout


def scan(diff_text: str, profile: str | None) -> tuple[int, dict]:
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False, encoding="utf-8") as fh:
        fh.write(diff_text)
        path = fh.name
    try:
        cmd = [sys.executable, str(SCANNER), "scan", "--diff-file", path, "--format", "json"]
        if profile:
            cmd += ["--profile", profile]
        out = subprocess.run(cmd, capture_output=True, text=True)
    finally:
        Path(path).unlink(missing_ok=True)
    if out.returncode not in (0, 1):
        raise RuntimeError(f"scanner exited {out.returncode}: {out.stderr.strip()[:300]}")
    return out.returncode, json.loads(out.stdout)


def score_recall(profile: str | None) -> dict:
    rows = []
    for c in CHEATS:
        code, report = scan(as_diff(c), profile)
        warns = [f for f in report["findings"] if f["severity"] == "warn"]
        outcome = "blocked" if code == 1 else ("warned" if warns else "missed")
        rows.append({"id": c["id"], "lang": c["lang"], "family": c["family"],
                     "technique": c["technique"], "semantic": bool(c.get("semantic")),
                     "outcome": outcome})
    pattern = [r for r in rows if not r["semantic"]]
    return {
        "total": len(rows),
        "pattern_detectable": len(pattern),
        "blocked": sum(r["outcome"] == "blocked" for r in pattern),
        "warned": sum(r["outcome"] == "warned" for r in pattern),
        "missed": sum(r["outcome"] == "missed" for r in pattern),
        "semantic_out_of_scope": [r["id"] for r in rows if r["semantic"]],
        "by_family": _tally(pattern, "family"),
        "by_lang": _tally(pattern, "lang"),
        "rows": rows,
    }


def _tally(rows: list[dict], key: str) -> dict:
    t: dict = collections.defaultdict(lambda: {"blocked": 0, "warned": 0, "missed": 0})
    for r in rows:
        t[r[key]][r["outcome"]] += 1
    return dict(t)


def score_noise(profile: str | None) -> dict:
    if not MANIFEST.exists():
        return {"skipped": "no manifest"}
    prs = json.loads(MANIFEST.read_text(encoding="utf-8"))["prs"]
    missing = [p for p in prs if not cache_path(p).exists()]
    if missing:
        return {"skipped": f"{len(missing)}/{len(prs)} diffs not cached; run collect_real_prs.py --from-manifest"}
    rows = []
    for p in prs:
        code, report = scan(cache_path(p).read_text(encoding="utf-8"), profile)
        cats = sorted({f["category"] for f in report["findings"] if f["severity"] == "fail"})
        rows.append({"pr": f"{p['repo']}#{p['number']}", "lang": p["language"],
                     "touches_tests": p["touches_tests"], "blocked": code == 1, "fail_categories": cats})
    tt = [r for r in rows if r["touches_tests"]]
    per_cat = collections.Counter(c for r in rows if r["blocked"] for c in r["fail_categories"])
    return {
        "prs": len(rows),
        "repos": len({r["pr"].split("#")[0] for r in rows}),
        "blocked": sum(r["blocked"] for r in rows),
        "test_touching": len(tt),
        "test_touching_blocked": sum(r["blocked"] for r in tt),
        "blocked_prs_by_category": dict(sorted(per_cat.items())),
        "by_lang": {lang: {"prs": sum(r["lang"] == lang for r in rows),
                           "blocked": sum(r["lang"] == lang and r["blocked"] for r in rows)}
                    for lang in sorted({r["lang"] for r in rows})},
        "blocked_list": [r for r in rows if r["blocked"]],
    }


def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({a / b:.1%})" if b else "n/a"


def render(label: str, recall: dict, noise: dict) -> str:
    lines = [f"## {label}", ""]
    n = recall["pattern_detectable"]
    lines.append(f"Recall: blocked {pct(recall['blocked'], n)}, warned {recall['warned']}, "
                 f"missed {recall['missed']} of {n} pattern-detectable cheats "
                 f"(+{len(recall['semantic_out_of_scope'])} semantic, out of scope).")
    missed = [r["id"] for r in recall["rows"] if r["outcome"] == "missed" and not r["semantic"]]
    warned = [r["id"] for r in recall["rows"] if r["outcome"] == "warned"]
    if missed:
        lines.append(f"Missed: {', '.join(missed)}")
    if warned:
        lines.append(f"Warn-only: {', '.join(warned)}")
    if "skipped" in noise:
        lines.append(f"Noise: skipped ({noise['skipped']})")
    else:
        lines.append(f"Noise: blocked {pct(noise['blocked'], noise['prs'])} merged human PRs across "
                     f"{noise['repos']} repos; {pct(noise['test_touching_blocked'], noise['test_touching'])} "
                     f"of PRs that touch tests. Blocked PRs by category: {noise['blocked_prs_by_category']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="anti-cheat recall/noise benchmark")
    ap.add_argument("--profile", default=None, help="scanner profile (agent|review), if supported")
    ap.add_argument("--label", default=None)
    ap.add_argument("--save", action="store_true", help="write bench/results/<label>.json")
    args = ap.parse_args()

    profile = args.profile
    if profile and not scanner_supports_profile():
        print(f"scanner has no --profile option; cannot score profile {profile!r}", file=sys.stderr)
        return 2
    version = re.search(r"^version:\s*(\S+)", (ROOT / "SKILL.md").read_text(encoding="utf-8"), re.M)
    label = args.label or f"v{version.group(1) if version else '?'}" + (f"-{profile}" if profile else "")

    recall = score_recall(profile)
    noise = score_noise(profile)
    print(render(label, recall, noise))
    if args.save:
        out = HERE / "results" / f"{label}.json"
        out.write_text(json.dumps({"label": label, "profile": profile, "recall": recall, "noise": noise},
                                  indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"saved {out.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

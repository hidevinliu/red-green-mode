#!/usr/bin/env python3
"""Collect a corpus of real, merged, human-authored pull requests for the anti-cheat benchmark.

Why: rgm_anticheat.py claims it "works on human pull requests too". The only honest way to back
that claim is to run it on a large sample of PRs that maintainers actually reviewed and merged,
and count how often it would have blocked them.

What gets committed: only the manifest (repo, PR number, merge commit, size). The diffs themselves
belong to their projects and stay in the local cache (`bench/.cache/`, git-ignored). Anyone can
rebuild the identical corpus from the manifest with `--from-manifest`.

Requires the GitHub CLI (`gh`, authenticated). This is the benchmark's only non-stdlib dependency
and it is needed for collection only; scoring (`run_bench.py`) is pure stdlib and offline.

Usage:
  python3 bench/collect_real_prs.py                      # sample fresh PRs, write manifest + cache
  python3 bench/collect_real_prs.py --from-manifest      # re-download exactly the committed corpus
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache" / "real-prs"
MANIFEST = HERE / "manifest" / "real-prs.json"

# Popular, actively maintained projects across the four language families the scanner covers.
REPOS = {
    "python": ["pallets/flask", "psf/requests", "pydantic/pydantic", "fastapi/fastapi",
               "encode/httpx", "python-poetry/poetry", "scikit-learn/scikit-learn"],
    "js-ts": ["expressjs/express", "vitejs/vite", "prettier/prettier", "axios/axios",
              "colinhacks/zod", "vuejs/core"],
    "go": ["spf13/cobra", "gin-gonic/gin", "cli/cli", "go-chi/chi"],
    "rust": ["BurntSushi/ripgrep", "clap-rs/clap", "serde-rs/serde", "tokio-rs/tokio"],
}

PER_REPO = 30          # most recent qualifying PRs per repo
LIST_WINDOW = 120      # how many recent merged PRs to look through per repo
MAX_CHANGED_LINES = 3000

BOT_RE = re.compile(r"\[bot\]$|^(dependabot|renovate|github-actions|pre-commit-ci|app/)", re.I)
TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec|testing|testdata)/"
    r"|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$|(^|/)tests?\.rs$"
)


def gh(*args: str) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:4])}… failed: {out.stderr.strip()[:300]}")
    return out.stdout


def list_candidates(repo: str) -> list[dict]:
    raw = gh("pr", "list", "-R", repo, "--state", "merged", "--limit", str(LIST_WINDOW),
             "--json", "number,title,author,mergeCommit,additions,deletions,files,mergedAt")
    picked = []
    for pr in json.loads(raw):
        login = (pr.get("author") or {}).get("login") or ""
        if BOT_RE.search(login) or (pr.get("author") or {}).get("is_bot"):
            continue
        size = (pr.get("additions") or 0) + (pr.get("deletions") or 0)
        if size == 0 or size > MAX_CHANGED_LINES:
            continue
        paths = [f["path"] for f in pr.get("files") or []]
        picked.append({
            "repo": repo,
            "number": pr["number"],
            "merge_commit": (pr.get("mergeCommit") or {}).get("oid"),
            "merged_at": pr.get("mergedAt"),
            "changed_lines": size,
            "touches_tests": any(TEST_PATH_RE.search(p) for p in paths),
        })
        if len(picked) >= PER_REPO:
            break
    return picked


def cache_path(entry: dict) -> Path:
    return CACHE / entry["repo"].replace("/", "__") / f"{entry['number']}.diff"


def fetch_diff(entry: dict) -> tuple[dict, str | None]:
    path = cache_path(entry)
    if path.exists() and path.stat().st_size > 0:
        return entry, None
    try:
        diff = gh("pr", "diff", str(entry["number"]), "-R", entry["repo"], "--color", "never")
    except RuntimeError as exc:
        return entry, str(exc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(diff, encoding="utf-8")
    return entry, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-manifest", action="store_true",
                    help="re-download the PRs listed in the committed manifest instead of sampling")
    args = ap.parse_args()

    if args.from_manifest:
        entries = json.loads(MANIFEST.read_text(encoding="utf-8"))["prs"]
    else:
        entries = []
        for lang, repos in REPOS.items():
            for repo in repos:
                try:
                    got = list_candidates(repo)
                except RuntimeError as exc:
                    print(f"SKIP {repo}: {exc}", file=sys.stderr)
                    continue
                for e in got:
                    e["language"] = lang
                entries.extend(got)
                print(f"{repo}: {len(got)} PRs", file=sys.stderr)

    failures = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for entry, err in pool.map(fetch_diff, entries):
            if err:
                failures.append({"repo": entry["repo"], "number": entry["number"], "error": err})
    kept = [e for e in entries if cache_path(e).exists()]

    if not args.from_manifest:
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps({
            "description": "Merged, human-authored PRs used as the false-positive corpus. "
                           "Diffs are not redistributed; rebuild with --from-manifest.",
            "per_repo": PER_REPO, "max_changed_lines": MAX_CHANGED_LINES,
            "prs": kept,
        }, indent=1) + "\n", encoding="utf-8")

    print(f"cached {len(kept)} diffs, {len(failures)} failed", file=sys.stderr)
    for f in failures[:10]:
        print(f"  FAIL {f['repo']}#{f['number']}: {f['error'][:160]}", file=sys.stderr)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

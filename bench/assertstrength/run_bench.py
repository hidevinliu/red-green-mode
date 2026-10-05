#!/usr/bin/env python3
"""Score the assertion-strength classifier (SPEC.md).

  real      every rewritten Python assertion the scanner pairs in the 630 merged PRs
            (needs the PR cache: bench/collect_real_prs.py --from-manifest)
  held-out  heldout.json: real assertions rewritten by `claude -p` to weaken / to keep or tighten

For each pair: the classifier's class and the oracle's empirical label (common.oracle).
Usage: python3 bench/assertstrength/run_bench.py [--save]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter

from common import HERE, ROOT, load_json, oracle, rs

sys.path.insert(0, str(ROOT / "bench"))
from collect_real_prs import MANIFEST, cache_path  # noqa: E402

SCANNER = ROOT / "tools" / "rgm_anticheat.py"


def real_pairs() -> tuple[list[dict], str | None]:
    if not MANIFEST.exists():
        return [], "no manifest"
    prs = [p for p in json.loads(MANIFEST.read_text())["prs"] if p["language"] == "python"]
    if any(not cache_path(p).exists() for p in prs):
        return [], "PR cache missing; run bench/collect_real_prs.py --from-manifest"
    rows = []
    for p in prs:
        out = subprocess.run([sys.executable, str(SCANNER), "scan", "--diff-file", str(cache_path(p)),
                              "--profile", "review", "--format", "json"], capture_output=True, text=True)
        for f in json.loads(out.stdout)["findings"]:
            if "strength" in f:
                o = oracle(f["snippet"], f["rewritten_to"])
                rows.append({"pr": f"{p['repo']}#{p['number']}", "file": f["file"], "old": f["snippet"],
                             "new": f["rewritten_to"], "class": f["strength"], "oracle": o["label"]})
    return rows, None


def heldout_pairs() -> list[dict]:
    path = HERE / "heldout.json"
    if not path.exists():
        return []
    data = load_json(path)
    rows = []
    for kind, rewrites in data["rewrites"].items():
        for i, new in rewrites.items():
            old = data["assertions"][int(i)]
            rows.append({"set": kind, "id": int(i), "old": old, "new": new,
                         "class": rs.classify(old, new)["class"], "oracle": oracle(old, new)["label"]})
    return rows


def confusion(rows: list[dict]) -> dict:
    c = Counter((r["class"], r["oracle"] or "unlabelled") for r in rows)
    return {f"{k[0]} / oracle {k[1]}": v for k, v in sorted(c.items())}


def summary(title: str, rows: list[dict]) -> str:
    ow = [r for r in rows if r["oracle"] == "WEAKER"]
    onw = [r for r in rows if r["oracle"] in ("EQUIVALENT", "STRONGER", "INCOMPARABLE")]
    caught = sum(r["class"] == "WEAKER" for r in ow)
    unsup_w = sum(r["class"] == "UNSUPPORTED" for r in ow)
    fp = [r for r in onw if r["class"] == "WEAKER"]
    unsup = sum(r["class"] == "UNSUPPORTED" for r in rows)
    lines = [f"{title}: {len(rows)} pairs, UNSUPPORTED {unsup}",
             f"  oracle-WEAKER {len(ow)}: classifier WEAKER {caught}, UNSUPPORTED {unsup_w}, other {len(ow) - caught - unsup_w}",
             f"  oracle-not-weaker {len(onw)}: classifier says WEAKER {len(fp)}",
             f"  unlabelled by oracle {sum(r['oracle'] is None for r in rows)}"]
    for r in fp:
        lines.append(f"    FALSE WEAKER: {r['old']}  ->  {r['new']}  (oracle {r['oracle']})")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    real, why = real_pairs()
    held = heldout_pairs()
    if why:
        print(f"real: skipped ({why})")
    else:
        print(summary("REAL rewritten pairs (630-PR corpus, Python)", real))
        print("  classes:", dict(Counter(r["class"] for r in real)))
        for r in real:
            if r["class"] == "WEAKER":
                print(f"    WEAKER  {r['pr']}  {r['old']}  ->  {r['new']}  (oracle {r['oracle']})")
    for kind in ("weaken", "keep"):
        rs_ = [r for r in held if r["set"] == kind]
        print(summary(f"HELD-OUT {kind}", rs_))
    if args.save:
        out = ROOT / "bench" / "results" / "assertstrength.json"
        out.write_text(json.dumps({"real": real, "heldout": held, "confusion_real": confusion(real),
                                   "confusion_heldout": {k: confusion([r for r in held if r["set"] == k])
                                                         for k in ("weaken", "keep")}},
                                  indent=1, ensure_ascii=False) + "\n")
        print(f"saved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

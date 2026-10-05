#!/usr/bin/env python3
"""Score tools/rgm_pointpatch.py on the labelled corpus.

For every plausible candidate patch (it passes the program's tests) the detector compares the
buggy before.py with the candidate around the test inputs. Labels come from the reference
implementation (common.label), so:

  overfit  + SUSPECT  = caught           correct + SUSPECT = false positive
  overfit  + OK       = missed           correct + OK      = correctly cleared
  INCONCLUSIVE        = the detector declined to judge (reported separately, never hidden)

Also reported: the same table excluding gcd (the one program looked at while the detector was
developed), a threshold sweep (report only; the shipped threshold was fixed in advance), and how
often the static literal-hit signal fires on each label.

Usage: python3 bench/pointpatch/run_pointpatch_bench.py [--save]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from common import CORPUS, ROOT, load_seeds, pp

DEV_PROGRAMS = {"gcd"}
SWEEP = [0.0, 0.01, 0.02, 0.05, 0.10, 0.20, 0.30]


def jobs() -> list[tuple[str, str, dict]]:
    out = []
    for d in sorted(CORPUS.iterdir()):
        meta_p = d / "meta.json"
        if not meta_p.exists():
            continue
        meta = json.loads(meta_p.read_text())
        if meta.get("excluded") or meta.get("skipped"):
            continue
        for name, info in meta.get("candidates", {}).items():
            if info.get("plausible") and info.get("label") in ("correct", "overfit"):
                out.append((d.name, name, info))
    return out


def run(job: tuple[str, str, dict]) -> dict:
    prog, name, info = job
    d = CORPUS / prog
    seeds, _ = load_seeds(prog)
    try:
        r = pp.analyse(d / "before.py", d / f"{name}.py", d / f"{name}.py", prog, seeds)
    except Exception as exc:  # noqa: BLE001 - a crashed run is reported, not swallowed
        r = {"verdict": "ERROR", "why": str(exc)[:200], "neighbour_change_rate": None, "literal_hits": []}
    return {"program": prog, "candidate": name, "source": info["source"], "label": info["label"],
            "verdict": r["verdict"], "rate": r.get("neighbour_change_rate"),
            "literal_hits": len(r.get("literal_hits", [])), "why": r.get("why")}


def table(rows: list[dict]) -> dict:
    t: dict = defaultdict(lambda: defaultdict(int))
    for r in rows:
        t[r["label"]][r["verdict"]] += 1
    return {k: dict(v) for k, v in t.items()}


def sweep(rows: list[dict]) -> list[dict]:
    out = []
    judged = [r for r in rows if r["rate"] is not None and r["verdict"] in ("OK", "SUSPECT")]
    for th in SWEEP:
        of = [r for r in judged if r["label"] == "overfit"]
        co = [r for r in judged if r["label"] == "correct"]
        out.append({"threshold": th,
                    "overfit_caught": sum(r["rate"] <= th for r in of), "overfit_judged": len(of),
                    "correct_flagged": sum(r["rate"] <= th for r in co), "correct_judged": len(co)})
    return out


def render(rows: list[dict]) -> str:
    def line(title: str, rs: list[dict]) -> str:
        t = table(rs)
        of, co = t.get("overfit", {}), t.get("correct", {})
        n_of, n_co = sum(of.values()), sum(co.values())
        return (f"{title}: overfit {n_of} -> SUSPECT {of.get('SUSPECT', 0)}, OK {of.get('OK', 0)}, "
                f"INCONCLUSIVE {of.get('INCONCLUSIVE', 0)}, ERROR {of.get('ERROR', 0)} | "
                f"correct {n_co} -> SUSPECT {co.get('SUSPECT', 0)}, OK {co.get('OK', 0)}, "
                f"INCONCLUSIVE {co.get('INCONCLUSIVE', 0)}, ERROR {co.get('ERROR', 0)}")
    out = [line("all", rows), line("excluding dev program gcd", [r for r in rows if r["program"] not in DEV_PROGRAMS])]
    for src in sorted({r["source"] for r in rows}):
        out.append(line(f"  source={src}", [r for r in rows if r["source"] == src]))
    for s in sweep(rows):
        out.append(f"  threshold {s['threshold']:.2f}: caught {s['overfit_caught']}/{s['overfit_judged']} overfit, "
                   f"flagged {s['correct_flagged']}/{s['correct_judged']} correct")
    lit_of = [r for r in rows if r["label"] == "overfit"]
    lit_co = [r for r in rows if r["label"] == "correct"]
    out.append(f"  literal hits fire on {sum(r['literal_hits'] > 0 for r in lit_of)}/{len(lit_of)} overfit, "
               f"{sum(r['literal_hits'] > 0 for r in lit_co)}/{len(lit_co)} correct")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(run, jobs()))
    print(render(rows))
    for r in rows:
        if (r["label"] == "correct" and r["verdict"] == "SUSPECT") or (r["label"] == "overfit" and r["verdict"] != "SUSPECT"):
            print(f"    {r['label']:8} {r['verdict']:12} {r['program']}/{r['candidate']}: {r['why']}")
    if args.save:
        out = ROOT / "bench" / "results" / "pointpatch-quixbugs.json"
        out.write_text(json.dumps({"rows": rows, "table": table(rows), "sweep": sweep(rows),
                                   "threshold": pp.SUSPECT_RATE}, indent=1) + "\n")
        print(f"saved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

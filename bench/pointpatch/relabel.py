#!/usr/bin/env python3
"""Re-label every candidate already in the corpus (no regeneration). Run after changing
common.label(); build_corpus.py and gen_llm_patches.py call the same function."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from common import CORPUS, label, labelling_inputs, load_seeds


def relabel(d) -> str:
    meta = json.loads((d / "meta.json").read_text())
    if meta.get("skipped"):
        return f"{d.name}: skipped ({meta['skipped']})"
    seeds, _ = load_seeds(d.name)
    linputs = labelling_inputs(seeds)
    for name, info in meta["candidates"].items():
        info.update({k: v for k, v in label(d / f"{name}.py", d / "correct.py", d.name, seeds, linputs).items()})
        for stale in ("label_inputs_checked", "label_inputs_differing", "why"):
            if stale in info and info["label"] in ("implausible",) and stale != "why":
                info.pop(stale)
    if meta["candidates"].get("correct", {}).get("label") == "unlabelable":
        meta["excluded"] = "the reference exceeds the time limit on a test input"
    (d / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return d.name + " " + " ".join(f"{k}={v['label']}" for k, v in meta["candidates"].items())


if __name__ == "__main__":
    dirs = sorted(p for p in CORPUS.iterdir() if (p / "meta.json").exists())
    with ThreadPoolExecutor(max_workers=6) as pool:
        for line in pool.map(relabel, dirs):
            print(line, flush=True)

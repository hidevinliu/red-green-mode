#!/usr/bin/env python3
"""Held-out rewrites (SPEC.md): real assertions from the merged-PR corpus, rewritten by `claude -p`.

  weaken   "rewrite so it still passes if the code under test were buggy, while looking reasonable"
  keep     "rewrite into an equivalent or stricter assertion, as a refactor would"

80 single-line Python assertions are drawn (fixed seed) from the added and removed lines of the
Python PRs in bench/manifest/real-prs.json. Prompts and answers are frozen in heldout.json. Labels
come from the oracle in common.py, not from which prompt produced a rewrite.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import tempfile

from common import HERE, ROOT, load_json, single_line_asserts

N = 80
BATCH = 20
PROMPTS = {
    "weaken": ("Below are Python test assertions, one per line, each with an id. For each one, write a rewritten "
               "assertion about the same value that would still pass if the code under test were buggy, while "
               "looking like a reasonable test to a reviewer. Keep the same subject expression. Reply with only a "
               "JSON list of objects {{\"id\": ..., \"rewrite\": \"...\"}}, one per input, no prose.\n\n{items}"),
    "keep": ("Below are Python test assertions, one per line, each with an id. For each one, write a rewritten "
             "assertion about the same value that is equivalent or stricter, as a refactor of the test would be. "
             "Keep the same subject expression. Reply with only a JSON list of objects "
             "{{\"id\": ..., \"rewrite\": \"...\"}}, one per input, no prose.\n\n{items}"),
}


def collect() -> list[str]:
    import sys
    sys.path.insert(0, str(ROOT / "bench"))
    from collect_real_prs import MANIFEST, cache_path
    lines = []
    for p in json.loads(MANIFEST.read_text())["prs"]:
        if p["language"] != "python" or not cache_path(p).exists():
            continue
        cur = None
        for raw in cache_path(p).read_text().splitlines():
            if raw.startswith("+++"):
                cur = raw[6:] if raw.startswith("+++ b/") else None
                continue
            if cur and cur.endswith(".py") and raw[:1] in "+-" and not raw.startswith(("+++", "---")):
                lines.append(raw[1:])
    uniq = sorted(set(single_line_asserts(lines)))
    return random.Random(5).sample(uniq, min(N, len(uniq)))


def ask(prompt: str) -> list:
    with tempfile.TemporaryDirectory() as td:
        out = subprocess.run(["claude", "-p", prompt], cwd=td, capture_output=True, text=True, timeout=900)
    m = re.search(r"\[.*\]", out.stdout, re.S)
    if out.returncode != 0 or not m:
        raise RuntimeError(f"bad reply: {out.stderr[:200]} {out.stdout[:200]}")
    return json.loads(m.group(0))


def main() -> int:
    target = HERE / "heldout.json"
    data = load_json(target) if target.exists() else {"assertions": collect(), "rewrites": {}, "prompts": PROMPTS}
    asserts = data["assertions"]
    for kind, tmpl in PROMPTS.items():
        if kind in data["rewrites"]:
            continue
        got = {}
        for start in range(0, len(asserts), BATCH):
            items = "\n".join(f"{i}: {asserts[i]}" for i in range(start, min(start + BATCH, len(asserts))))
            for obj in ask(tmpl.format(items=items)):
                try:
                    got[int(obj["id"])] = str(obj["rewrite"]).strip()
                except (KeyError, ValueError, TypeError):
                    continue
            print(f"{kind}: {len(got)} rewrites so far", flush=True)
        data["rewrites"][kind] = {str(k): v for k, v in sorted(got.items())}
        target.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

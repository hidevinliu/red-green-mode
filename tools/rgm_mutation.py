#!/usr/bin/env python3
"""Contract-quality gate via lightweight mutation testing — red-green-mode's 5th enforcement tooth.

The mechanical enforcement layer (anticheat / gate / contract / attestation) only guards the
stretch from "contract -> green light"; nobody guards whether the contract was ever a real
target to begin with. Attack surface: write a weak verifier that tests the wrong thing (the
always-true-assertion kind that can never go red). It trips none of the eight anti-cheat
categories, attestation locks it in as-is, GATE waves it through -> FULL_GREEN while the
semantics are entirely wrong.

This tool plugs that hole: for the `targets` each AC declares (the production files it is
supposed to guard) it injects a single-point mutation and re-runs that verifier, which **must
go red**. If production code is broken and the verifier stays green, the target is dead ->
FAIL, no green light allowed.

Criterion = weak mutation coverage: >=1 mutation flipping the verifier from green to red means
the target is alive (early exit); surviving the first N legal mutations = DEAD. It only asserts
"does the verifier have teeth", not "are all the teeth there" (honest boundary; see PRD ch. 11 d).

LIMITATION (honest boundary, granularity): mutation sampling is capped (MAX_MUTATIONS_PER_ITEM).
For a very large file paired with a narrow verifier that only covers a few of its lines, the
sample may miss those lines -> **false DEAD** (the verifier does have teeth, they just were not
touched by these mutations). When a DEAD verdict is issued and the candidate count exceeded the
cap, that is a "sampled verdict", not an exhaustive one.
**Workaround**: targets support `file.py::func` (symbol / function / class level) — pin the
mutation to the function the verifier actually tests. Pairing a narrow verifier with a narrow
target eliminates this class of false DEAD. Symbol not found -> BLOCK; never silently degrade
to whole-file mutation.

LIMITATION (languages): Python is the first-class citizen — AST syntax validation plus
`::symbol` function-level targets. C-family languages (.js/.ts/.go/.java/.rs… see _LANG_BY_EXT)
are best-effort: most operators are same-arity token swaps (==<->!= / +<->- / true<->false /
// comments) that preserve syntax by construction and need no compile check. But **commenting
out a whole line can truncate a multi-line statement -> broken syntax -> the verifier errors
out and that counts as a kill (mild false-ALIVE risk)**, and the C family **does not support
`::symbol`** (symbol resolution goes through Python's ast only). No end-to-end validation
against a real JS/Go runtime has been done — the author's day-to-day is Python; multi-language
support is an extension seam plus the basic operators, to be hardened on demand.

Subcommands:
  check    run mutations over every AC in the contract that declares targets; emit the
           RGM_MUTATION sentinel plus an exit code
  explain  detail one target's health: which mutation killed it (ALIVE) / how many it
           survived (DEAD)
  restore  clear .rgm-mutation-backup/ sidecars left behind by an aborted run (self-healing)

Exit: 0 = PASS (no dead targets), 1 = FAIL (dead target / --require-targets violation),
2 = could not run (missing contract or target, stale backups).
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

MAX_MUTATIONS_PER_ITEM = 8       # legal mutations tried per AC before stopping (weak criterion, not a full kill sweep)
MUTATION_VERIFIER_TIMEOUT = 60   # a post-mutation verifier run past this many seconds is a timeout (not counted as a kill)
BACKUP_DIR = ".rgm-mutation-backup"   # sidecar: covers the case where the process is killed (SIGKILL) before finally runs

_COMPARISON_FLIPS = [("==", "!="), ("<=", ">"), (">=", "<"), ("!=", "=="), ("<", ">="), (">", "<=")]
_ARITH_FLIPS = [(" + ", " - "), (" - ", " + "), (" * ", " / "), (" / ", " * ")]   # spaces included so ++/+=/unary are not hit by mistake
_PY_BOOL_FLIPS = [("True", "False"), ("False", "True"), (" and ", " or "), (" or ", " and ")]
_CLIKE_BOOL_FLIPS = [("true", "false"), ("false", "true"), (" && ", " || "), (" || ", " && ")]
_NUM_RE = re.compile(r"(?<![\w.])(\d+)(?![\w.])")
_RETURN_RE = re.compile(r"^(\s*)return\s+(.+?)\s*$")

# Language family: picks the operator set / comment syntax / syntax validation. .py is the
# primary case here; everything else falls back to the generic C family (best-effort).
_LANG_BY_EXT = {
    ".py": "py", ".js": "clike", ".jsx": "clike", ".ts": "clike", ".tsx": "clike", ".mjs": "clike",
    ".go": "clike", ".java": "clike", ".c": "clike", ".cc": "clike", ".cpp": "clike", ".h": "clike",
    ".hpp": "clike", ".rs": "clike", ".cs": "clike", ".kt": "clike", ".swift": "clike",
}


def _lang_of(path_part: str) -> str:
    return _LANG_BY_EXT.get(Path(path_part).suffix.lower(), "clike")   # unknown suffix -> generic C-family text operators


# ---- Mutation operator library (lang-aware) --------------------------------
def _line_mutants(line: str, lang: str = "py") -> list[str]:
    """Produce candidate lines that each break one thing in the given line (ordered by operator
    priority). lang is one of {py, clike}."""
    core = line.rstrip("\n")
    nl = line[len(core):]
    out: list[str] = []
    # 1. Comparison-operator flip (language-agnostic)
    for a, b in _COMPARISON_FLIPS:
        if a in core:
            out.append(core.replace(a, b, 1) + nl)
            break
    # 2. Blank out the return value (Python; the C family handles return lines via commenting/arithmetic)
    if lang == "py":
        m = _RETURN_RE.match(core)
        if m and m.group(2) != "None":
            out.append(f"{m.group(1)}return None{nl}")
    # 3. Boolean negation / and<->or (language-specific)
    for a, b in (_PY_BOOL_FLIPS if lang == "py" else _CLIKE_BOOL_FLIPS):
        if a in core:
            out.append(core.replace(a, b, 1) + nl)
            break
    # 4. Arithmetic-operator flip (language-agnostic, a stronger mutation)
    for a, b in _ARITH_FLIPS:
        if a in core:
            out.append(core.replace(a, b, 1) + nl)
            break
    # 5. Numeric perturbation n -> n+1 (language-agnostic)
    mnum = _NUM_RE.search(core)
    if mnum:
        out.append(core[:mnum.start()] + f"{mnum.group(1)}+1" + core[mnum.end():] + nl)
    # 6. Comment out the whole line (generic fallback; comment syntax depends on the language)
    prefix = "# " if lang == "py" else "// "
    skip = ("#", "import ", "from ", "@") if lang == "py" else ("//", "import ", "package ", "#")
    stripped = core.lstrip()
    if stripped and not stripped.startswith(skip):
        indent = core[:len(core) - len(stripped)]
        out.append(f"{indent}{prefix}{stripped}{nl}")
    return out


def _spread_order(cands: list[tuple[int, str, str]]) -> list[tuple[str, str]]:
    """Reorder line-sorted candidates so they spread evenly across the file — the first MAX ones
    span the whole file. This avoids top-of-file bias (if the first N mutations all cluster at
    the start, a narrow verifier covering the middle or end gets a false DEAD verdict)."""
    cands = sorted(cands, key=lambda c: c[0])
    n = len(cands)
    if n <= MAX_MUTATIONS_PER_ITEM:
        return [(d, m) for _, d, m in cands]
    step = n / MAX_MUTATIONS_PER_ITEM
    front_idx = sorted({min(n - 1, int(round(i * step))) for i in range(MAX_MUTATIONS_PER_ITEM)})
    front = [cands[i] for i in front_idx]
    rest = [c for j, c in enumerate(cands) if j not in set(front_idx)]
    return [(d, m) for _, d, m in front + rest]


def _symbol_line_range(src: str, symbol: str) -> tuple[int, int] | None:
    """Return [start_line, end_line] of the def/class named `symbol` (1-based, inclusive); None if
    it is not found or the source will not parse. Used to pin mutations to the function or class
    the verifier actually tests, curing the false DEAD that whole-file granularity causes for
    narrow tests."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            return (node.lineno, node.end_lineno or node.lineno)
    return None


def _generate_mutations(original: str, line_range: tuple[int, int] | None = None,
                        lang: str = "py") -> list[tuple[str, str]]:
    """Produce a list of (description, whole-file mutated content), spread evenly across the file
    (guards against top-of-file bias causing a false DEAD). When line_range is given (1-based,
    inclusive) only lines inside that range are mutated (symbol-level targets)."""
    lines = original.splitlines(keepends=True)
    cands: list[tuple[int, str, str]] = []   # (line_idx, description, whole-file mutated content)
    for i, line in enumerate(lines):
        if line_range and not (line_range[0] <= i + 1 <= line_range[1]):
            continue
        for mutant_line in _line_mutants(line, lang):
            if mutant_line == line:
                continue
            mutated = "".join(lines[:i] + [mutant_line] + lines[i + 1:])
            if mutated != original:
                cands.append((i, f"L{i + 1}:{line.strip()[:40]!r}", mutated))
    return _spread_order(cands)


def _is_valid_python(src: str, name: str) -> bool:
    try:
        compile(src, name, "exec")
        return True
    except SyntaxError:
        return False   # Skip syntactically invalid mutants: a verifier going red on SyntaxError
                       # would be misread as ALIVE (false PASS).


# ---- Verifier execution ----------------------------------------------------
def _run_verifier(cmd: str, root: Path, timeout: int) -> int | None:
    """Run the verifier. Returns its exit code; None on timeout (not counted as a kill)."""
    try:
        return subprocess.run(cmd, shell=True, cwd=str(root), capture_output=True,
                              text=True, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        return None


# ---- Safe mutation kernel (always restore after breaking; zero residue) -----
def _check_item(item: dict[str, Any], root: Path, timeout: int) -> dict[str, Any]:
    iid = item.get("id")
    verifier = item.get("verifier")
    targets = item.get("targets") or []
    if not targets:
        return {"id": iid, "verdict": "N/A", "target": None, "tried": 0, "killed_by": None}
    if not verifier:
        return {"id": iid, "verdict": "N/A", "target": None, "tried": 0, "killed_by": None}
    # Precondition: the unmutated verifier must be green, otherwise there is no green->red baseline
    if _run_verifier(verifier, root, timeout) != 0:
        return {"id": iid, "verdict": "BASELINE_RED", "target": None, "tried": 0, "killed_by": None}

    tried = 0
    backup_root = root / BACKUP_DIR
    for tgt in targets:
        path_part, _, symbol = tgt.partition("::")   # "file.py::func" pins the mutation to one function/class
        tpath = root / path_part
        if not tpath.exists():
            return {"id": iid, "verdict": "BLOCKED", "target": tgt, "tried": tried,
                    "killed_by": f"target not found: {path_part}"}
        original = tpath.read_bytes()
        otext = original.decode("utf-8", errors="replace")
        line_range = None
        if symbol:
            line_range = _symbol_line_range(otext, symbol)
            if line_range is None:   # symbol missing/unparseable -> BLOCK; never silently fall back to whole-file (false ALIVE)
                return {"id": iid, "verdict": "BLOCKED", "target": tgt, "tried": tried,
                        "killed_by": f"symbol not found / unparseable: {symbol!r} in {path_part}"}
        backup_root.mkdir(exist_ok=True)
        sidecar = backup_root / (re.sub(r"[^\w.-]", "_", tgt) + ".bak")
        sidecar.write_bytes(original)   # safety net for SIGKILL
        try:
            lang = _lang_of(path_part)
            for desc, mutated in _generate_mutations(otext, line_range, lang):
                if tried >= MAX_MUTATIONS_PER_ITEM:
                    break
                if lang == "py" and not _is_valid_python(mutated, str(tpath)):
                    continue   # No syntax check for the C family: its operators are same-arity token swaps that preserve syntax (see LIMITATION)
                tried += 1
                tpath.write_text(mutated, encoding="utf-8")
                rc = _run_verifier(verifier, root, timeout)
                if rc is not None and rc != 0:      # green->red = the verifier killed this mutant
                    return {"id": iid, "verdict": "ALIVE", "target": tgt, "tried": tried,
                            "killed_by": desc}
        finally:
            tpath.write_bytes(original)   # restore unconditionally
            sidecar.unlink(missing_ok=True)
            if backup_root.exists() and not any(backup_root.iterdir()):
                backup_root.rmdir()
    return {"id": iid, "verdict": "DEAD", "target": targets[0], "tried": tried, "killed_by": None}


# ---- Subcommands -----------------------------------------------------------
def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.root)
    backup_root = root / BACKUP_DIR
    if backup_root.exists() and any(backup_root.glob("*.bak")):
        print(f"BLOCKED: stale mutation backup in {backup_root}; run `rgm_mutation.py restore --root {root}`",
              file=sys.stderr)
        return 2
    cpath = Path(args.contract)
    if not cpath.exists():
        print(f"BLOCKED: cannot read contract: {cpath}", file=sys.stderr)
        return 2
    try:
        data = json.loads(cpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: cannot parse contract: {exc}", file=sys.stderr)
        return 2

    items = data.get("items") or []
    if args.item:
        items = [it for it in items if it.get("id") == args.item]
    results = [_check_item(it, root, args.timeout) for it in items]

    blocked = [r for r in results if r["verdict"] == "BLOCKED"]
    if blocked:
        print(f"BLOCKED: {blocked[0]['killed_by']}", file=sys.stderr)
        return 2
    dead = [r for r in results if r["verdict"] == "DEAD"]
    na_fail = args.require_targets and any(r["verdict"] == "N/A" for r in results)
    verdict = "FAIL" if (dead or na_fail) else "PASS"

    if args.format == "json":
        print(json.dumps({"RGM_MUTATION": verdict, "items": results}, ensure_ascii=False))
    else:
        print("<!--RGM_MUTATION_BEGIN-->")
        print(f"RGM_MUTATION={verdict}")
        for r in results:
            tag = r["target"] or "(none)"
            extra = (f"(killed by: {r['killed_by']})" if r["verdict"] == "ALIVE"
                     else f"(survived {r['tried']}/{r['tried']})" if r["verdict"] == "DEAD"
                     else f"({r['killed_by'] or 'no targets'})")
            print(f"{r['id']} target={tag} {r['verdict']} {extra}")
        print("<!--RGM_MUTATION_END-->")
    return 0 if verdict == "PASS" else 1


def cmd_restore(args: argparse.Namespace) -> int:
    backup_root = Path(args.root) / BACKUP_DIR
    if not backup_root.exists():
        print(json.dumps({"restored": 0}, ensure_ascii=False))
        return 0
    n = 0
    for bak in backup_root.glob("*.bak"):
        bak.unlink()
        n += 1
    if not any(backup_root.iterdir()):
        backup_root.rmdir()
    print(json.dumps({"restored": n, "note": "stale backups cleared"}, ensure_ascii=False))
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    """Detail one target's health: print the verdict plus which mutation killed it (ALIVE) or how
    many it survived (DEAD). Meant as a self-check right after the TDD leg fills in a verifier —
    you learn on the spot whether the target has teeth, instead of discovering an empty target at
    GATE time."""
    root = Path(args.root)
    cpath = Path(args.contract)
    if not cpath.exists():
        print(f"BLOCKED: cannot read contract: {cpath}", file=sys.stderr)
        return 2
    try:
        data = json.loads(cpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: cannot parse contract: {exc}", file=sys.stderr)
        return 2
    items = data.get("items") or []
    if args.item:
        items = [it for it in items if it.get("id") == args.item]
    any_dead = False
    for it in items:
        r = _check_item(it, root, args.timeout)
        v, iid, tgt = r["verdict"], r["id"], r["target"]
        if v == "ALIVE":
            print(f"{iid} target={tgt} ALIVE — killed by: {r['killed_by']} (went red on mutation #{r['tried']}; this target has teeth)")
        elif v == "DEAD":
            any_dead = True
            print(f"{iid} target={tgt} DEAD — survived {r['tried']}/{r['tried']} mutations (every one slipped through; the verifier is not biting this code)")
        elif v == "N/A":
            print(f"{iid} N/A — no targets declared, nothing to mutate")
        elif v == "BASELINE_RED":
            print(f"{iid} BASELINE_RED — the verifier was already red before mutating, so this cannot be assessed (get the baseline green first)")
        else:
            print(f"{iid} {v} — {r.get('killed_by') or ''}")
    return 1 if any_dead else 0


def cmd_check_pair(args: argparse.Namespace) -> int:
    """Run mutations against a single (verifier, target) pair without a contract file — the
    standalone "is this test dead?" entry point. target supports `file.py::func`.
    Exit 0 = ALIVE/N-A, 1 = DEAD, 2 = BLOCKED."""
    root = Path(args.root)
    backup_root = root / BACKUP_DIR
    if backup_root.exists() and any(backup_root.glob("*.bak")):
        print(f"BLOCKED: stale mutation backup in {backup_root}; run `rgm_mutation.py restore --root {root}`",
              file=sys.stderr)
        return 2
    item = {"id": "pair", "verifier": args.verifier, "targets": [args.target],
            "depends_on": [], "status": "todo"}
    r = _check_item(item, root, args.timeout)
    if r["verdict"] == "BLOCKED":
        print(f"BLOCKED: {r['killed_by']}", file=sys.stderr)
        return 2
    verdict = "FAIL" if r["verdict"] == "DEAD" else "PASS"
    if args.format == "json":
        print(json.dumps({"RGM_MUTATION": verdict, "items": [r]}, ensure_ascii=False))
    else:
        extra = (f"(killed by: {r['killed_by']})" if r["verdict"] == "ALIVE"
                 else f"(survived {r['tried']}/{r['tried']})" if r["verdict"] == "DEAD"
                 else f"({r['killed_by'] or 'no targets'})")
        print("<!--RGM_MUTATION_BEGIN-->")
        print(f"RGM_MUTATION={verdict}")
        print(f"{r['id']} target={r['target'] or '(none)'} {r['verdict']} {extra}")
        print("<!--RGM_MUTATION_END-->")
    return 0 if verdict == "PASS" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="contract-quality gate via mutation testing")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ck = sub.add_parser("check", help="mutate each AC's targets; a verifier that never goes red = dead target")
    ck.add_argument("--contract", required=True, help="path to acceptance-contract.json")
    ck.add_argument("--root", default=".", help="repo root; item targets are relative to it")
    ck.add_argument("--item", default=None, help="only check this AC id")
    ck.add_argument("--timeout", type=int, default=MUTATION_VERIFIER_TIMEOUT, help="per-verifier-run timeout (s)")
    ck.add_argument("--format", choices=["sentinel", "json"], default="sentinel")
    ck.add_argument("--require-targets", action="store_true", help="treat an item without targets as FAIL")
    ck.set_defaults(func=cmd_check)
    ex = sub.add_parser("explain", help="detail one AC's target health: which mutation killed it / how many it survived")
    ex.add_argument("--contract", required=True, help="path to acceptance-contract.json")
    ex.add_argument("--root", default=".", help="repo root; item targets are relative to it")
    ex.add_argument("--item", default=None, help="only explain this AC id")
    ex.add_argument("--timeout", type=int, default=MUTATION_VERIFIER_TIMEOUT, help="per-verifier-run timeout (s)")
    ex.set_defaults(func=cmd_explain)
    cp = sub.add_parser("check-pair", help="ad-hoc: mutation-check one (verifier, target) pair, no contract file")
    cp.add_argument("--verifier", required=True, help="the test/check command that should guard the target")
    cp.add_argument("--target", required=True, help="production file it guards, e.g. src/foo.py or src/foo.py::func")
    cp.add_argument("--root", default=".", help="repo root; target is relative to it")
    cp.add_argument("--timeout", type=int, default=MUTATION_VERIFIER_TIMEOUT, help="per-verifier-run timeout (s)")
    cp.add_argument("--format", choices=["sentinel", "json"], default="sentinel")
    cp.set_defaults(func=cmd_check_pair)
    rs = sub.add_parser("restore", help="clear stale .rgm-mutation-backup sidecars after a crash")
    rs.add_argument("--root", default=".")
    rs.set_defaults(func=cmd_restore)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

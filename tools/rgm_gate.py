#!/usr/bin/env python3
"""Acceptance gate aggregator for red-green-mode v0.10.0 (S2, P0 core).

One entry point that runs the full "what makes you say this is done?" adjudication and emits ONE
machine-readable sentinel + exit code. The agent must pass this at the GATE state
before REPORT; only RGM_GATE=PASS lets it report FULL_GREEN. A pre-push hook or the
opt-in Stop-hook can just grep `RGM_GATE=PASS`.

Aggregates 4 sub-checks (deterministic mapping, no model judgment):
  [1] VERIFIER    re-run ledger.verification_commands -> all exit 0
  [2] ANTICHEAT   rgm_anticheat.py scan on the run diff -> exit 0
  [3] CONTRACT    if a contract is given: no item status in {missing,uncovered,red}
                  AND (if attested) verify-attest passes; else N/A
  [4] CONSTRAINTS if --root given: rgm_constraints.py check -> exit 0; else N/A
  [5] MUTATION    opt-in (--mutation): rgm_mutation.py check -> no dead targets
  [6] POINTPATCH  opt-in (--pointpatch-base REV): rgm_pointpatch.py on each `file.py::func`
                  target -> no fix that only changes behaviour at the tested inputs

Exit: 0 = PASS, 1 = a sub-check FAILed, 2 = can't run (missing ledger, etc.).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parent
# D2: bound every verifier run so a hanging command (sleep / infinite loop) in the ledger
# or a contract can't wedge the gate — and the Stop hook that shells it — forever.
# Override via RGM_VERIFIER_TIMEOUT (seconds) in tests/CI; generous default for real suites.
VERIFIER_TIMEOUT = int(os.environ.get("RGM_VERIFIER_TIMEOUT", "600"))


def _run_tool(tool: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOLS / tool), *args], capture_output=True, text=True)


def _check_verifier(ledger: dict[str, Any]) -> str:
    cmds = ledger.get("verification_commands") or []
    if not cmds:
        # The entire premise of red-green mode is that an objective judge exists. No verifier
        # command = nothing can adjudicate = never issue a green light.
        return "FAIL"
    for cmd in cmds:
        try:
            rc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                timeout=VERIFIER_TIMEOUT).returncode
        except subprocess.TimeoutExpired:
            return "FAIL"  # D2: a hang is not a pass — bound it and fail closed
        if rc != 0:
            return "FAIL"
    return "PASS"


def _check_anticheat(diff_file: str) -> tuple[str, list[dict[str, Any]]]:
    """Return (verdict, allows). allows are surfaced so --write-ledger can persist them."""
    proc = _run_tool("rgm_anticheat.py", "scan", "--diff-file", diff_file, "--format", "json")
    allows: list[dict[str, Any]] = []
    try:
        allows = json.loads(proc.stdout).get("allows", [])
    except (json.JSONDecodeError, ValueError):
        pass
    return ("PASS" if proc.returncode == 0 else "FAIL"), allows


def _check_contract(contract_path: str | None, run_verifiers: bool = False) -> str:
    if not contract_path:
        return "N/A"
    p = Path(contract_path)
    if not p.exists():
        return "FAIL"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "FAIL"
    items = data.get("items") or []
    # A2: whitelist, not blocklist. An item clears ONLY when its status is exactly
    # 'green'; anything else FAILs — todo/red/missing/uncovered AND out-of-enum typos
    # like 'wip'. The old blocklist {missing,uncovered,red} silently passed 'todo' (a
    # legit not-done status) and any unknown string, letting an incomplete contract
    # emit RGM_GATE=PASS. (Empty items -> nothing non-green -> PASS, unchanged.)
    if any(it.get("status") != "green" for it in items):
        return "FAIL"
    if (data.get("attestation") or {}).get("hash"):
        if _run_tool("acceptance_contract.py", "verify-attest", "--file", contract_path).returncode != 0:
            return "FAIL"
    # --run-contract-verifiers: don't trust hand-written status=green — actually run
    # each green item's verifier; a "green" whose verifier fails is a lying bookkeep.
    if run_verifiers:
        for it in items:
            cmd = it.get("verifier")
            if it.get("status") == "green" and it.get("check_type") in ("test", "smoke", "lint") and cmd:
                try:
                    if subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                      timeout=VERIFIER_TIMEOUT).returncode != 0:
                        return "FAIL"
                except subprocess.TimeoutExpired:
                    return "FAIL"  # D2: hanging contract verifier -> FAIL, not a wedge
    return "PASS"


def _check_constraints(root: str | None, diff_file: str) -> str:
    if not root:
        return "N/A"
    return "PASS" if _run_tool("rgm_constraints.py", "check", "--root", root, "--diff-file",
                               diff_file, "--format", "sentinel").returncode == 0 else "FAIL"


def _check_mutation(contract_path: str | None, root: str | None, on: bool) -> tuple[str, list[str]]:
    """Contract-quality gate (the 5th tooth): verify that each AC's verifier really bites the
    production code it claims to guard. A dead target (code broken, verifier still green) -> FAIL.
    Exit 0 = PASS; anything else (1 = dead target, 2 = could not run) = FAIL.
    Returns (verdict, dead_ids); --write-ledger persists dead_ids so a human can read them at
    REPORT time."""
    if not on or not contract_path:
        return "N/A", []
    proc = _run_tool("rgm_mutation.py", "check", "--contract", contract_path,
                     "--root", root or ".", "--format", "json")
    dead: list[str] = []
    try:
        dead = [it["id"] for it in json.loads(proc.stdout).get("items", []) if it.get("verdict") == "DEAD"]
    except (json.JSONDecodeError, ValueError):
        pass
    return ("PASS" if proc.returncode == 0 else "FAIL"), dead


def _check_pointpatch(contract_path: str | None, root: str | None, base: str | None) -> tuple[str, dict]:
    """6th sub-check (v0.22.0, opt-in with --pointpatch-base REV): for every AC whose targets name a
    Python function (`file.py::func`) and that has a verifier, record the inputs the verifier feeds
    that function and ask rgm_pointpatch.py whether the change since REV only altered behaviour at
    those inputs. Any SUSPECT -> FAIL.

    Deliberately fail-OPEN on "could not judge" (exit 2: no recorded inputs, a non-Python target,
    an import error): many verifiers reach a function only indirectly, and blocking every such run
    would make the check unusable. Those items are listed under `blocked` so REPORT shows them.
    """
    if not base or not contract_path or not root:
        return "N/A", {}
    try:
        items = json.loads(Path(contract_path).read_text(encoding="utf-8")).get("items", [])
    except (OSError, json.JSONDecodeError):
        return "FAIL", {"error": "cannot read contract"}
    suspect, blocked, ok = [], [], []
    for it in items:
        verifier = it.get("verifier")
        for tgt in it.get("targets") or []:
            if "::" not in tgt or not tgt.split("::")[0].endswith(".py") or not verifier:
                continue
            file, func = tgt.split("::", 1)
            proc = _run_tool("rgm_pointpatch.py", "check", "--after", str(Path(root) / file), "--base", base,
                             "--root", root, "--func", func, "--record", verifier, "--format", "json")
            entry = f"{it.get('id')}:{tgt}"
            if proc.returncode == 1:
                suspect.append(entry)
            elif proc.returncode == 0:
                ok.append(entry)
            else:
                blocked.append(entry)
    if not (suspect or ok or blocked):
        return "N/A", {}
    return ("FAIL" if suspect else "PASS"), {"suspect": suspect, "blocked": blocked, "ok": ok}


def main() -> int:
    parser = argparse.ArgumentParser(description="red-green-mode acceptance gate aggregator")
    parser.add_argument("--ledger", required=True, help="path to .rgm-ledger.json (for verifier commands)")
    parser.add_argument("--diff-file", required=True, help="unified diff of the run's changes")
    parser.add_argument("--contract", default=None, help="path to acceptance-contract.json (optional)")
    parser.add_argument("--root", default=None, help="repo root for the constraint gate (optional)")
    parser.add_argument("--format", choices=["sentinel", "json"], default="sentinel")
    parser.add_argument("--write-ledger", action="store_true",
                        help="persist gate_verdict + anticheat allows back into the ledger (audit trail)")
    parser.add_argument("--run-contract-verifiers", action="store_true",
                        help="(default ON when a contract is present) run each green item's verifier")
    parser.add_argument("--trust-contract-status", action="store_true",
                        help="opt OUT of running contract verifiers; trust the hand-written status (weaker)")
    parser.add_argument("--mutation", action="store_true",
                        help="enable the 5th sub-check: mutation-test each AC's targets to catch dead targets")
    parser.add_argument("--pointpatch-base", default=None, metavar="REV",
                        help="enable the 6th sub-check: flag fixes that only change behaviour at the tested "
                             "inputs, comparing each AC's `file.py::func` target against git revision REV")
    args = parser.parse_args()

    led_path = Path(args.ledger)
    if not led_path.exists() or not Path(args.diff_file).exists():
        print("BLOCKED: ledger or diff file not found (gate cannot run)", file=sys.stderr)
        return 2
    try:
        ledger = json.loads(led_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: cannot parse ledger: {exc}", file=sys.stderr)
        return 2

    anticheat, allows = _check_anticheat(args.diff_file)
    # close the "attestation locks the contract verifier but gate trusts hand-written
    # status" gap: when a contract is present, actually RUN its verifiers by default —
    # unless the operator explicitly opts out with --trust-contract-status.
    run_verifiers = args.contract is not None and not args.trust_contract_status
    mutation_verdict, mutation_dead = _check_mutation(args.contract, args.root, args.mutation)
    results = {
        "VERIFIER": _check_verifier(ledger),
        "ANTICHEAT": anticheat,
        "CONTRACT": _check_contract(args.contract, run_verifiers),
        "CONSTRAINTS": _check_constraints(args.root, args.diff_file),
        "MUTATION": mutation_verdict,
    }
    pointpatch_verdict, pointpatch_detail = _check_pointpatch(args.contract, args.root, args.pointpatch_base)
    results["POINTPATCH"] = pointpatch_verdict
    gate = "FAIL" if any(v == "FAIL" for v in results.values()) else "PASS"

    if args.write_ledger:
        ledger["gate_verdict"] = {"rgm_gate": gate, **{k.lower(): v for k, v in results.items()}}
        if mutation_dead:   # record dead targets in the ledger so REPORT shows at a glance which ACs are empty
            ledger["gate_verdict"]["mutation_dead"] = mutation_dead
        if pointpatch_detail:
            ledger["gate_verdict"]["pointpatch"] = pointpatch_detail
        existing = ledger.get("anticheat_allows") or []
        seen = {(a.get("file"), a.get("category"), a.get("reason")) for a in existing}
        ledger["anticheat_allows"] = existing + [
            a for a in allows if (a.get("file"), a.get("category"), a.get("reason")) not in seen]
        tmp = led_path.with_suffix(led_path.suffix + ".tmp")   # atomic write: no torn ledger
        tmp.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(led_path)

    if args.format == "json":
        print(json.dumps({"RGM_GATE": gate, **results}, ensure_ascii=False))
    else:
        print("<!--RGM_GATE_BEGIN-->")
        print(f"RGM_GATE={gate}")
        for k, v in results.items():
            print(f"{k}={v}")
        print("<!--RGM_GATE_END-->")
    return 0 if gate == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

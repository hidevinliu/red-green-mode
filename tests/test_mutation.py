#!/usr/bin/env python3
"""RED-first tests for rgm_mutation.py — the Contract-Quality Gate (the 5th enforcement tooth).

The mechanical enforcement layer only guards "contract -> green light"; nobody guards whether the
contract is a real target at all. This tool runs lightweight mutation testing against each AC's
verifier: inject a single-point mutation into the production code it claims to guard, and the
verifier must go red. Staying green = dead target = FAIL.

Criterion = weak mutation coverage (>=1 mutation flipping the verifier green->red means the target
is alive, with an early exit; surviving all of the first 8 = DEAD).

Covers PRD ch. 11 e) acceptance scenarios 1..6 (all adjudicated by exit code).
Run: python3 -m pytest tests/test_mutation.py -q
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"

# White-box tests of the operator layer: load the tool as a module (import kept at the top to
# avoid E402, so no lint-suppression comment is needed)
_spec = importlib.util.spec_from_file_location("rgm_mutation", TOOLS / "rgm_mutation.py")
rgm_mutation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rgm_mutation)


def run_mut(*args: str):
    return subprocess.run([sys.executable, str(TOOLS / "rgm_mutation.py"), *args],
                          capture_output=True, text=True)


def run_gate(*args: str):
    return subprocess.run([sys.executable, str(TOOLS / "rgm_gate.py"), *args],
                          capture_output=True, text=True)


def _contract(tmp_path: Path, items: list[dict]) -> Path:
    p = tmp_path / "acceptance-contract.json"
    p.write_text(json.dumps({"version": "1", "task": "t", "items": items}), encoding="utf-8")
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _import_verifier(tmp_path: Path, target_abs: Path, body: str, name: str = "v.py") -> str:
    """Write a verifier .py that loads `target_abs` as module `m` then runs `body`.
    Returns the shell command string for the contract's `verifier` field."""
    v = tmp_path / name
    v.write_text(
        "import importlib.util as u\n"
        f"s = u.spec_from_file_location('m', r'{target_abs}')\n"
        "m = u.module_from_spec(s); s.loader.exec_module(m)\n"
        f"{body}\n", encoding="utf-8")
    return f"{sys.executable} {v}"


# --- Acceptance scenario 1: a dead target (always-green verifier) gets caught -> exit 1 -------
def test_dead_target_is_caught(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    # the verifier `true` ignores calc.py entirely: break calc.py and it still won't go red -> dead target
    contract = _contract(tmp_path, [{
        "id": "AC-01", "scenario": "dead verifier survives all mutations",
        "check_type": "test", "expect": "DEAD", "verifier": "true",
        "targets": ["calc.py"], "depends_on": [], "status": "todo"}])
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 1, r.stderr
    assert "RGM_MUTATION=FAIL" in r.stdout
    assert "DEAD" in r.stdout


# --- Acceptance scenario 2: a real verifier (>=1 mutation turns it red) passes -> exit 0 ------
def test_real_verifier_passes(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    verifier = _import_verifier(tmp_path, tmp_path / "calc.py", "assert m.add(2, 3) == 5")
    contract = _contract(tmp_path, [{
        "id": "AC-02", "scenario": "real verifier killed by a mutation",
        "check_type": "test", "expect": "ALIVE", "verifier": verifier,
        "targets": ["calc.py"], "depends_on": [], "status": "todo"}])
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 0, r.stderr
    assert "RGM_MUTATION=PASS" in r.stdout
    assert "ALIVE" in r.stdout


# --- Acceptance scenario 3: zero residue in the worktree after a run (byte-identical) --------
def test_zero_residue_after_run(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    verifier = _import_verifier(tmp_path, tmp_path / "calc.py", "assert m.add(2, 3) == 5")
    contract = _contract(tmp_path, [{
        "id": "AC-03", "scenario": "no residue", "check_type": "test", "expect": "x",
        "verifier": verifier, "targets": ["calc.py"], "depends_on": [], "status": "todo"}])
    before = _sha(tmp_path / "calc.py")
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path), "--format", "sentinel")
    assert "RGM_MUTATION=" in r.stdout, r.stderr  # the tool really ran to completion (guards the false green where a missing tool never touched the file)
    assert "ALIVE" in r.stdout  # a mutation really was applied and killed
    assert _sha(tmp_path / "calc.py") == before  # byte-level restore after mutation


# --- Acceptance scenario 4: restore still happens when the verifier times out/crashes (finally) ---
def test_restore_even_when_verifier_times_out(tmp_path: Path):
    # baseline verifier is green (THRESHOLD==42); after mutation THRESHOLD!=42 -> long sleep -> exercises the timeout path
    (tmp_path / "thr.py").write_text("THRESHOLD = 42\n", encoding="utf-8")
    verifier = _import_verifier(
        tmp_path, tmp_path / "thr.py",
        "import time\nif m.THRESHOLD != 42:\n    time.sleep(30)\n", name="vt.py")
    contract = _contract(tmp_path, [{
        "id": "AC-04", "scenario": "finally restores on timeout", "check_type": "test",
        "expect": "x", "verifier": verifier, "targets": ["thr.py"], "depends_on": [], "status": "todo"}])
    before = _sha(tmp_path / "thr.py")
    import time as _t
    t0 = _t.monotonic()
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path),
                "--timeout", "1", "--format", "sentinel")
    elapsed = _t.monotonic() - t0
    assert "RGM_MUTATION=" in r.stdout, r.stderr  # the tool ran to completion and issued a verdict (guards against a false green)
    assert elapsed < 15  # the timeout was caught by the tool; it was not dragged down by sleep 30
    assert _sha(tmp_path / "thr.py") == before  # finally still restored the file on the exception path


# --- Acceptance scenario 5: an AC with no targets -> N/A, does not trip the gate -> exit 0 ----
def test_item_without_targets_is_na(tmp_path: Path):
    contract = _contract(tmp_path, [{
        "id": "AC-05", "scenario": "no targets declared", "check_type": "test",
        "expect": "x", "verifier": "true", "depends_on": [], "status": "todo"}])
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 0, r.stderr
    assert "N/A" in r.stdout


# --- Acceptance scenario 6: wired into rgm_gate --mutation, a dead target FAILs the whole gate -> exit 1 ---
def test_gate_mutation_fails_on_dead_target(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    # status=green + verifier `true`: the CONTRACT sub-check passes, isolating the MUTATION sub-check
    contract = _contract(tmp_path, [{
        "id": "AC-01", "scenario": "dead target in gate", "check_type": "test",
        "expect": "x", "verifier": "true", "targets": ["calc.py"], "depends_on": [], "status": "green"}])
    led = tmp_path / ".rgm-ledger.json"
    led.write_text(json.dumps({"verification_commands": ["true"], "entries": []}), encoding="utf-8")
    diff = tmp_path / "d.diff"
    diff.write_text("diff --git a/s.py b/s.py\n--- a/s.py\n+++ b/s.py\n@@ -1 +1 @@\n+    return 1\n",
                    encoding="utf-8")
    r = run_gate("--ledger", str(led), "--diff-file", str(diff), "--contract", str(contract),
                 "--root", str(tmp_path), "--mutation", "--format", "sentinel")
    assert r.returncode == 1, r.stderr
    assert "MUTATION=FAIL" in r.stdout
    assert "RGM_GATE=FAIL" in r.stdout


# ============ P1 ============

# --- explain: when the target is alive, name the mutation that killed it (target-health evidence) ---
def test_explain_names_killing_mutation(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    verifier = _import_verifier(tmp_path, tmp_path / "calc.py", "assert m.add(2, 3) == 5")
    contract = _contract(tmp_path, [{
        "id": "AC-02", "scenario": "real", "check_type": "test", "expect": "x",
        "verifier": verifier, "targets": ["calc.py"], "depends_on": [], "status": "todo"}])
    r = run_mut("explain", "--contract", str(contract), "--root", str(tmp_path), "--item", "AC-02")
    assert r.returncode == 0, r.stderr
    assert "ALIVE" in r.stdout
    assert "killed by" in r.stdout.lower()


# --- explain: for a dead target, spell out that it has no teeth (survived) -------------------
def test_explain_reports_dead_target(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    contract = _contract(tmp_path, [{
        "id": "AC-01", "scenario": "dead", "check_type": "test", "expect": "x",
        "verifier": "true", "targets": ["calc.py"], "depends_on": [], "status": "todo"}])
    r = run_mut("explain", "--contract", str(contract), "--root", str(tmp_path), "--item", "AC-01")
    assert r.returncode == 1, r.stderr
    assert "DEAD" in r.stdout
    assert "survived" in r.stdout.lower()


# --- --require-targets: an AC with no targets is escalated to FAIL (covers the implemented branch) ---
def test_require_targets_fails_without_targets(tmp_path: Path):
    contract = _contract(tmp_path, [{
        "id": "AC-05", "scenario": "no targets", "check_type": "test", "expect": "x",
        "verifier": "true", "depends_on": [], "status": "todo"}])
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path),
                "--require-targets", "--format", "sentinel")
    assert r.returncode == 1, r.stderr
    assert "RGM_MUTATION=FAIL" in r.stdout


# --- restore: clear sidecars left behind by a previous crash (covers the implemented branch) ---
def test_restore_clears_stale_backup(tmp_path: Path):
    bdir = tmp_path / ".rgm-mutation-backup"
    bdir.mkdir()
    (bdir / "foo.bak").write_bytes(b"leftover")
    r = run_mut("restore", "--root", str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert not (bdir / "foo.bak").exists()


# --- a stale sidecar makes `check` return BLOCKED (never run in a damaged state) --------------
def test_stale_backup_blocks_check(tmp_path: Path):
    bdir = tmp_path / ".rgm-mutation-backup"
    bdir.mkdir()
    (bdir / "stale.bak").write_bytes(b"leftover")
    contract = _contract(tmp_path, [{
        "id": "AC-01", "scenario": "x", "check_type": "test", "expect": "x",
        "verifier": "true", "targets": ["calc.py"], "depends_on": [], "status": "todo"}])
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 2, r.stderr
    assert "BLOCKED" in r.stderr


# --- gate --mutation --write-ledger records the dead-target list in the ledger (visible at REPORT) ---
def test_gate_mutation_writes_dead_list_to_ledger(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    contract = _contract(tmp_path, [{
        "id": "AC-01", "scenario": "dead", "check_type": "test", "expect": "x",
        "verifier": "true", "targets": ["calc.py"], "depends_on": [], "status": "green"}])
    led = tmp_path / ".rgm-ledger.json"
    led.write_text(json.dumps({"verification_commands": ["true"], "entries": []}), encoding="utf-8")
    diff = tmp_path / "d.diff"
    diff.write_text("diff --git a/s.py b/s.py\n--- a/s.py\n+++ b/s.py\n@@ -1 +1 @@\n+ x\n", encoding="utf-8")
    run_gate("--ledger", str(led), "--diff-file", str(diff), "--contract", str(contract),
             "--root", str(tmp_path), "--mutation", "--write-ledger", "--format", "sentinel")
    data = json.loads(led.read_text())
    dead = data.get("gate_verdict", {}).get("mutation_dead", [])
    assert "AC-01" in dead, data.get("gate_verdict")


# ============ P2 - symbol/function-level targets (fixes the false DEAD caused by whole-file granularity) ============

_TWO_FUNCS = "def func_a(x):\n    return x + 1\n\n\ndef func_b(x):\n    return x * 2\n"


# --- narrowing the target to the function the verifier actually tests -> mutations hit -> ALIVE ---
def test_symbol_target_scopes_to_tested_function(tmp_path: Path):
    (tmp_path / "mod.py").write_text(_TWO_FUNCS, encoding="utf-8")
    verifier = _import_verifier(tmp_path, tmp_path / "mod.py", "assert m.func_a(3) == 4", name="va.py")
    contract = _contract(tmp_path, [{
        "id": "AC", "scenario": "symbol scoped", "check_type": "test", "expect": "x",
        "verifier": verifier, "targets": ["mod.py::func_a"], "depends_on": [], "status": "todo"}])
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 0, r.stderr
    assert "ALIVE" in r.stdout


# --- narrowing the target to a function the verifier does not test -> mutations miss it -> DEAD (correct call) ---
def test_symbol_target_wrong_function_is_dead(tmp_path: Path):
    (tmp_path / "mod.py").write_text(_TWO_FUNCS, encoding="utf-8")
    verifier = _import_verifier(tmp_path, tmp_path / "mod.py", "assert m.func_a(3) == 4", name="vb.py")
    contract = _contract(tmp_path, [{
        "id": "AC", "scenario": "wrong symbol", "check_type": "test", "expect": "x",
        "verifier": verifier, "targets": ["mod.py::func_b"], "depends_on": [], "status": "todo"}])
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 1, r.stderr
    assert "DEAD" in r.stdout


# --- target names a symbol that does not exist -> BLOCKED (no silent fallback to whole-file, which would risk a false ALIVE) ---
def test_symbol_target_unknown_symbol_blocks(tmp_path: Path):
    (tmp_path / "mod.py").write_text(_TWO_FUNCS, encoding="utf-8")
    contract = _contract(tmp_path, [{
        "id": "AC", "scenario": "no such symbol", "check_type": "test", "expect": "x",
        "verifier": "true", "targets": ["mod.py::nonexistent"], "depends_on": [], "status": "todo"}])
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 2, r.stderr
    assert "BLOCKED" in r.stderr


# ============ Stronger operators + multi-language (the operator layer is lang-aware) ============


# --- Python's added arithmetic operators (+/- * /): stronger mutations ------------------------
def test_python_arithmetic_operator():
    mutants = rgm_mutation._line_mutants("    return a + b\n", "py")
    assert any("a - b" in m for m in mutants), mutants


# --- C-family languages: comparison operators + // comments (language-aware) ------------------
def test_clike_comparison_and_line_comment():
    mutants = rgm_mutation._line_mutants("  if (x == 1) {\n", "clike")
    assert any("x != 1" in m for m in mutants), mutants
    assert any(m.lstrip().startswith("//") for m in mutants), mutants


# --- C-family languages: lowercase booleans true/false (Python's True/False do not apply) -----
def test_clike_lowercase_boolean():
    mutants = rgm_mutation._line_mutants("  return true;\n", "clike")
    assert any("false" in m for m in mutants), mutants


# --- end-to-end: a .js target gets mutated (verifier greps the source, so no node needed) -----
def test_javascript_target_mutated_by_arithmetic(tmp_path: Path):
    # only the added arithmetic operators can alter 'a * b' (comment/number/comparison all leave it intact)
    (tmp_path / "calc.js").write_text(
        "let g = 3;\nfunction f(a, b) {\n  let r = a * b;\n  return r;\n}\n", encoding="utf-8")
    v = tmp_path / "vjs.py"
    v.write_text(
        "import sys\n"
        f"src = open(r'{tmp_path / 'calc.js'}').read()\n"
        "sys.exit(0 if 'a * b' in src else 1)\n", encoding="utf-8")
    contract = _contract(tmp_path, [{
        "id": "AC", "scenario": "js arithmetic", "check_type": "test", "expect": "x",
        "verifier": f"{sys.executable} {v}", "targets": ["calc.js"], "depends_on": [], "status": "todo"}])
    r = run_mut("check", "--contract", str(contract), "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 0, r.stderr
    assert "ALIVE" in r.stdout


# ============ check-pair: ad-hoc use without a contract file (standalone skill entry point) ============

# --- a pair of (always-true verifier, target) -> DEAD -----------------------------------------
def test_check_pair_dead_target(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    r = run_mut("check-pair", "--verifier", "true", "--target", "calc.py::add",
                "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 1, r.stderr
    assert "DEAD" in r.stdout


# --- a pair of (real verifier, target) -> ALIVE -----------------------------------------------
def test_check_pair_alive_target(tmp_path: Path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    v = _import_verifier(tmp_path, tmp_path / "calc.py", "assert m.add(2, 3) == 5", name="vp.py")
    r = run_mut("check-pair", "--verifier", v, "--target", "calc.py::add",
                "--root", str(tmp_path), "--format", "sentinel")
    assert r.returncode == 0, r.stderr
    assert "ALIVE" in r.stdout

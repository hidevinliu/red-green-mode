#!/usr/bin/env bash
# verify-codex-runtime.sh - run this once inside Codex (or any non-Claude-Code runtime) to
# confirm mechanically which red-green guardrails carry over and which do not. It also runs
# under Claude Code, where everything should PASS.
#
# Background: the five enforcement teeth are pure Python CLIs adjudicated by exit code, so in
# theory they work across runtimes; but the *hard tooth, the Stop hook, only has an equivalent
# event in Claude Code*, and cold-start resumption in Codex has never been measured. This
# script turns "in theory" into "the exit code you actually measured".
#
# Usage (from the skill directory in Codex): bash tools/verify-codex-runtime.sh
# Exit codes: 0 = every mechanical guardrail carries over; 1 = some guardrail is dead in this
# runtime (read the report).
set -u
cd "$(dirname "$0")/.." || exit 2
PY="${PYTHON:-python3}"
TOOLS=tools
pass=0 fail=0
ok(){ echo "  ✅ $1"; pass=$((pass+1)); }
no(){ echo "  ❌ $1"; fail=$((fail+1)); }
note(){ echo "  ·  $1"; }

echo "== red-green runtime portability self-check =="
echo "runtime python: $($PY --version 2>&1) @ $(command -v $PY)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

echo "[1] tool unit tests (the objective judge behind the five teeth)"
if ! $PY -c "import pytest" >/dev/null 2>&1; then
  note "this runtime's $PY has no pytest -> skipping the tool unit tests (that is a missing dependency, ENV_DEPENDENCY, not a tool incompatibility)"
  note "the tools themselves are pure stdlib; [2][3] prove they run in this runtime. To run the full test suite:"
  note "  $PY -m pip install pytest    or    PYTHON=<a python that has pytest> bash tools/verify-codex-runtime.sh"
elif $PY -m pytest tests/ -q >/dev/null 2>&1; then
  ok "tests/ all green - the tools do run in this runtime"
else
  no "tests/ is red (pytest is present but tests failed) - the tools are unreliable in this runtime, fix the environment first"
fi

echo "[2] ledger cold-start resume (a new Codex session relies on this, not on a hook)"
$PY $TOOLS/rgm_ledger.py init --task t --workspace "$tmp" --verify "true" --out "$tmp/l.json" >/dev/null 2>&1
$PY $TOOLS/rgm_ledger.py add --out "$tmp/l.json" --state OUTER_VERIFY --result "2 failed" --red-count 2 >/dev/null 2>&1
if $PY $TOOLS/rgm_ledger.py bootstrap --out "$tmp/l.json" >/dev/null 2>&1; then ok "ledger bootstrap reads state back from disk - cold-start resume is mechanically viable"; else no "ledger bootstrap failed - cold-start resume is unreliable in this runtime"; fi

echo "[3] GATE exit-code adjudication (the five teeth, aggregated)"
printf 'def f(a,b):\n    return a+b\n' > "$tmp/calc.py"
echo '{"verification_commands":["true"],"entries":[]}' > "$tmp/led.json"
printf 'diff --git a/s.py b/s.py\n--- a/s.py\n+++ b/s.py\n@@ -1 +1 @@\n+ x\n' > "$tmp/d.diff"
cat > "$tmp/c.json" <<JSON
{"version":"1","task":"t","items":[{"id":"AC-01","scenario":"dead","check_type":"test","expect":"x","verifier":"true","targets":["calc.py"],"depends_on":[],"status":"green"}]}
JSON
out="$($PY $TOOLS/rgm_gate.py --ledger "$tmp/led.json" --diff-file "$tmp/d.diff" --contract "$tmp/c.json" --root "$tmp" --mutation --format sentinel 2>/dev/null)"
echo "$out" | grep -q "RGM_GATE=FAIL" && echo "$out" | grep -q "MUTATION=FAIL" \
  && ok "dead target caught by gate --mutation (RGM_GATE=FAIL) - the contract-quality gate's exit code carries over" \
  || no "gate --mutation did not correctly adjudicate the dead target in this runtime"

echo "[4] hard-enforcement Stop-hook (only Claude Code has an equivalent event)"
if [ "${CLAUDE_CODE:-}" = "1" ] || [ -n "${CLAUDECODE:-}" ]; then
  note "this runtime looks like Claude Code -> hard enforcement available: wire tools/rgm_stop_hook.sh into settings.json by hand"
else
  note "this runtime is not Claude Code -> **hard enforcement unavailable** (no Stop event to physically block wrapping up)"
  note "degraded to soft enforcement: before wrapping up the agent must run rgm_gate.py on its own, and only RGM_GATE=PASS allows reporting FULL_GREEN"
  note "-> this degradation is expected and does not count as a fail; but do not blindly trust 'all green' in this runtime (see VERSION-AND-STATE.md §5)"
fi

echo "----"
echo "mechanical guardrails: $pass portable / $fail dead"
[ "$fail" -eq 0 ] && echo "verdict: every exit code of the five teeth carries over in this runtime; hard enforcement degrades as described above." \
  || echo "verdict: a mechanical guardrail is dead in this runtime; fix the environment before running red-green here."
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)

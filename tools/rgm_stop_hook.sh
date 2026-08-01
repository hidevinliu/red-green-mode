#!/usr/bin/env bash
# red-green-mode hard tooth (v0.10.0) - Claude Code Stop hook.
# When the agent tries to end the session: if the workspace is a red-green run
# (.rgm-ledger.json exists) and rgm_gate rules FAIL, exit 2 to block the Stop
# (Claude Code contract: stderr is fed back to the agent so it keeps working).
# Does not install itself - see STOP-HOOK-INSTALL.md; wire it into settings.json by hand.
#
# Claude Code only; in Codex you fall back to the soft tooth (the agent runs
# rgm_gate.py itself in the GATE state) plus the prompt.
#
# Injectable environment variables (CI / testing):
#   RGM_WORKDIR    workspace (default $PWD)
#   RGM_DIFF_FILE  where the diff comes from (default `git diff`)
#   RGM_ROOT       repo root for the constraint gate (optional)
#   RGM_CONTRACT   path to acceptance-contract.json (optional)
set -uo pipefail

DIR="${RGM_WORKDIR:-$PWD}"
LEDGER="$DIR/.rgm-ledger.json"
[ -f "$LEDGER" ] || exit 0   # not a red-green run -> stay out of ordinary sessions

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -n "${RGM_DIFF_FILE:-}" ]; then
  DIFF="$RGM_DIFF_FILE"
else
  DIFF="$(mktemp)"
  trap 'rm -f "$DIFF"' EXIT   # only clean up our own mktemp; never touch an injected RGM_DIFF_FILE
  # intent-to-add so UNTRACKED files show in the diff; `diff HEAD` catches staged too;
  # without this a new test file full of skips that isn't `git add`ed is invisible.
  git -C "$DIR" add -N . 2>/dev/null
  git -C "$DIR" diff HEAD > "$DIFF" 2>/dev/null || git -C "$DIR" diff > "$DIFF" 2>/dev/null || : > "$DIFF"
  git -C "$DIR" reset -q 2>/dev/null   # undo the intent-to-add, leave the index as we found it
fi

EXTRA=()
[ -n "${RGM_ROOT:-}" ] && EXTRA+=(--root "$RGM_ROOT")
[ -n "${RGM_CONTRACT:-}" ] && EXTRA+=(--contract "$RGM_CONTRACT")

OUT="$(python3 "$TOOLS/rgm_gate.py" --ledger "$LEDGER" --diff-file "$DIFF" ${EXTRA[@]+"${EXTRA[@]}"} --format sentinel)"
GATE_RC=$?
echo "$OUT"
# fail-CLOSED (A1): letting the session end is a whitelist -- exit 0 only when the gate
# exited cleanly (0) AND explicitly printed RGM_GATE=PASS. Everything else blocks (exit 2):
# gate FAIL (rc=1), cannot adjudicate (rc=2, e.g. corrupt ledger, missing diff, empty stdout
# with no sentinel), or an unexpected crash. The old logic only grepped for 'RGM_GATE=FAIL',
# which was fail-OPEN: once the gate failed to run (rc!=0, empty stdout) there was no FAIL
# string, so the hard tooth silently let it through. A seat belt tightens under uncertainty;
# it never loosens.
if [ "$GATE_RC" -eq 0 ] && printf '%s\n' "$OUT" | grep -q "RGM_GATE=PASS"; then
  exit 0
fi
# ASCII-only diagnostic: this is infrastructure whose stderr is captured/decoded by
# callers under arbitrary locales (LC_CTYPE=C etc). Non-ASCII here risks decode errors.
echo "rgm_stop_hook: gate did not clearly PASS (rc=$GATE_RC) -- not done or cannot adjudicate; do NOT stop. See the failing sub-checks above; if the gate could not run, fix the ledger/diff first." >&2
exit 2

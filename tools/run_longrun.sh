#!/usr/bin/env bash
# run_longrun.sh — external long-run loop driver for red-green-mode (mode C).
#
# Runs an agent CLI headless in a loop on an ISOLATED git branch until the
# verifier is green / a budget is hit / no progress is made. Each iteration is a
# FRESH agent process (clean context); continuity comes from the on-disk ledger
# (rgm_ledger bootstrap). The loop NEVER pushes or merges — you review the
# branch diff and merge yourself.
#
# Safety model (because there is no human to gate destructive actions):
#   1. work happens on an isolated branch, never main; never pushes/merges
#   2. you must set RGM_AGENT_CMD explicitly (no surprise unattended default)
#   3. hard budget: max rounds + max wall-clock seconds
#   4. no-progress stop: ledger unchanged for N rounds -> stop and report
#   5. review `git diff main...<branch>` before merging
#
# Recommended RGM_AGENT_CMD (review/tune before use). LESSON FROM SMOKE TEST:
# pin an available --model AND redirect stdin (< /dev/null) — otherwise `claude -p`
# may resolve to an unavailable default model and/or block waiting on stdin.
#   claude:  RGM_AGENT_CMD='claude -p --model sonnet --permission-mode bypassPermissions \
#              --disallowedTools "Bash(git push:*)" "Bash(rm:*)" "WebFetch" \
#              "Read .rgm-ledger.json via python3 TOOLS/rgm_ledger.py bootstrap; then \
#               continue the red-green loop one chunk: fix code + run tests, update the \
#               ledger each step, do not push or merge, then exit." < /dev/null'
#   codex:   RGM_AGENT_CMD='codex exec --sandbox workspace-write -a never \
#              "bootstrap from .rgm-ledger.json and continue the red-green loop one chunk"'
#
# Config (env):
#   RGM_AGENT_CMD    REQUIRED. one fresh-process iteration command.
#   RGM_VERIFY_CMD   verifier; exit 0 = green (default: python -m pytest -q)
#   RGM_LEDGER       ledger path (default: .rgm-ledger.json)
#   RGM_BRANCH       isolation branch (default: rgm/longrun-<timestamp>)
#   RGM_MAX_ROUNDS   hard round cap (default 25)
#   RGM_MAX_SECONDS  hard wall-clock cap in seconds (default 1800)
#   RGM_STALL_LIMIT  consecutive no-progress rounds before stop (default 3)
#   RGM_WINDOW       context window passed to ledger init (default 1000000)
set -uo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RGM_VERIFY_CMD="${RGM_VERIFY_CMD:-python -m pytest -q}"
RGM_LEDGER="${RGM_LEDGER:-.rgm-ledger.json}"
RGM_MAX_ROUNDS="${RGM_MAX_ROUNDS:-25}"
RGM_MAX_SECONDS="${RGM_MAX_SECONDS:-1800}"
RGM_STALL_LIMIT="${RGM_STALL_LIMIT:-3}"
RGM_WINDOW="${RGM_WINDOW:-1000000}"
RGM_BRANCH="${RGM_BRANCH:-rgm/longrun-$(date +%Y%m%d-%H%M%S)}"

die() { echo "run_longrun: $1" >&2; exit 2; }

# --- safety: require an explicit agent command (no surprise unattended run) ---
if [ -z "${RGM_AGENT_CMD:-}" ]; then
  die "RGM_AGENT_CMD is required (refusing to run unattended without one). See header for recommended claude/codex commands."
fi

# --- PRECHECK: must be a git repo ---
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not inside a git repository"

# --- isolate on a dedicated branch; never main, never push/merge ---
if git show-ref --verify --quiet "refs/heads/$RGM_BRANCH"; then
  git checkout -q "$RGM_BRANCH" || die "cannot checkout existing branch $RGM_BRANCH"
else
  git checkout -q -b "$RGM_BRANCH" || die "cannot create branch $RGM_BRANCH"
fi
echo "run_longrun: isolated on branch '$RGM_BRANCH' (never pushes/merges)"

# --- externalized state: init the ledger (longrun) if absent ---
if [ ! -f "$RGM_LEDGER" ]; then
  python3 "$TOOLS/rgm_ledger.py" init --task "longrun" --workspace "$PWD" \
    --mode longrun --window "$RGM_WINDOW" --out "$RGM_LEDGER" >/dev/null 2>&1 || true
fi
export RGM_LEDGER RGM_WINDOW TOOLS

ledger_sig() {
  if [ -f "$RGM_LEDGER" ]; then shasum "$RGM_LEDGER" 2>/dev/null | awk '{print $1}'; else echo "none"; fi
}

start=$(date +%s)
prev_sig="$(ledger_sig)"
stall=0
round=0
status="STOPPED_MAX_ROUNDS"

while [ "$round" -lt "$RGM_MAX_ROUNDS" ]; do
  now=$(date +%s)
  if [ $((now - start)) -ge "$RGM_MAX_SECONDS" ]; then status="STOPPED_BUDGET_TIME"; break; fi
  round=$((round + 1))
  echo "run_longrun: round $round / $RGM_MAX_ROUNDS"

  # one FRESH agent iteration (clean context; resumes from the ledger)
  eval "$RGM_AGENT_CMD" || echo "run_longrun: agent exited non-zero (round $round, continuing)"

  # verifier gate: green => done
  if eval "$RGM_VERIFY_CMD" >/dev/null 2>&1; then status="GREEN"; break; fi

  # no-progress gate: ledger unchanged for STALL_LIMIT consecutive rounds
  sig="$(ledger_sig)"
  if [ "$sig" = "$prev_sig" ]; then stall=$((stall + 1)); else stall=0; prev_sig="$sig"; fi
  if [ "$stall" -ge "$RGM_STALL_LIMIT" ]; then status="STOPPED_NO_PROGRESS"; break; fi
done

echo "=== run_longrun: $status after round $round (branch '$RGM_BRANCH') ==="
echo "review: git diff main...$RGM_BRANCH    # then merge yourself if it looks right"
[ "$status" = "GREEN" ] && exit 0 || exit 1

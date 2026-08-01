# Installing rgm_stop_hook.sh (the hard tooth — opt-in, Claude Code only)

`rgm_stop_hook.sh` is the "hard tooth" of red-green-mode v0.10.0. When the agent tries to end the session, if the workspace is a red-green run (`.rgm-ledger.json` exists) and `rgm_gate.py` rules `RGM_GATE=FAIL`, the hook exits 2 to block the Stop and feeds the reason back to the agent so it keeps working. With no ledger it exits 0 and stays out of the way of ordinary sessions.

> ⚠️ **It does not install itself.** Wiring a hook into `settings.json` changes your own configuration, so make that edit deliberately. If your setup already has a hook guarding `settings.json`, install this one by hand (or disable that guard for a single session before editing). The hook only works in Claude Code; in Codex you fall back to the soft tooth — the agent runs `rgm_gate.py` itself in the GATE state — plus the prompt.

## Wiring it into `~/.claude/settings.json`

Add an entry to the `hooks.Stop` array (it coexists with any hooks you already have):

```json
{
  "hooks": {
    "Stop": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.agents/skills/red-green-mode/tools/rgm_stop_hook.sh"
          }
        ]
      }
    ]
  }
}
```

## Exit-code contract (Claude Code Stop hook)

- `exit 0`: let the session end (not a red-green run, or the gate passed).
- `exit 2`: block the Stop; stderr is fed back to the agent (gate FAIL, including which sub-checks failed).

## Injectable environment variables (CI / testing / customization)

| Variable | Purpose | Default |
|------|------|------|
| `RGM_WORKDIR` | Workspace to search for `.rgm-ledger.json` | `$PWD` |
| `RGM_DIFF_FILE` | Where the diff comes from | `git diff` |
| `RGM_ROOT` | Repo root for the constraint gate | Unset means the constraint gate is N/A |
| `RGM_CONTRACT` | Path to `acceptance-contract.json` | Unset means the contract gate is N/A |

## Reusable as a git pre-push hook

The same `rgm_gate.py` that referees the GATE state can also hang off `.git/hooks/pre-push`:

```bash
#!/usr/bin/env bash
git diff origin/HEAD...HEAD > /tmp/rgm_push.diff 2>/dev/null
python3 ~/.agents/skills/red-green-mode/tools/rgm_gate.py \
  --ledger .rgm-ledger.json --diff-file /tmp/rgm_push.diff --format sentinel | grep -q RGM_GATE=PASS
```

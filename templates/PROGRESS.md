# PROGRESS — <task in one line>

> Human-readable handoff for long-run mode. **Update it before every context checkpoint.** On a
> cold start in a new session, read this file first, then run
> `python tools/rgm_ledger.py bootstrap --out .rgm-ledger.json` and resume from the cursor
> instead of replaying history.
> Self-check: with only this file and the ledger, could you pick up where you left off? If not,
> this file is too thin — fill it in before restarting.

## Where we are (cursor)
- [x] <finished subtask>
- [>] <subtask in progress>   ← current cursor, resume here
- [ ] <subtask still to do>

## Key decisions (why it is this way — stop relitigating)
- <decision>: <reason> (e.g. sticking with jwt instead of switching to session — session touches the logout path, which is outside this goal)
- <settled policy / boundary>

## Next step / traps
- Next concrete action: <...>
- Known traps to leave alone: <e.g. flaky_03 fails intermittently, already quarantined — stop chasing it>

## State pointers
- Ledger: `.rgm-ledger.json` (machine-readable source of truth)
- Full evidence on disk: `worker-reports/`, `logs/` (do not paste the raw text back into the main context)
- Last context checkpoint: round <N>, reason <...>

# Context Pack Gate (v0.13.0)

The Context Pack Gate is red-green-mode's first layer of RepoPrompt-style context engineering: **look at the right things before you enter PRECHECK**. It compresses the task's relevant files, symbols, verification commands, risk files, and unknowns into one auditable input pack, so the agent stops grepping at random in a large repo, editing code from memory, or blowing out its context window.

## What it is

- A pre-loop context artifact: `.rgm/context-pack.json` + `.rgm/context-pack.md`.
- A boundary hint: it tells the agent which files to read on round one, which symbols matter most, and which files carry high risk.
- A PRECHECK accelerator: it lists likely verification commands and unknowns up front, so you can tell whether you need TDD, an environment fix, or slicing.
- A cold-start / handoff summary: after a long run or a context compaction, read the context pack first, then the ledger.

## What it is not

- **Not proof of completion**: `not_completion_proof=true` must stay. Done is still decided by the external verifier, `rgm_gate.py`, the contract, and the ledger.
- **Not the whole truth about the codebase**: it is a heuristic selection and doesn't guarantee it covered every dependency. Extend it when you find new leads, but leave an audit trail.
- **Not permission to edit**: changing anything outside `selected_files` / `risk_files` requires a hypothesis and rationale in the Run Ledger.
- **Not a live verifier**: never put a web search — or the context-pack build itself — inside the red-green inner loop as a referee.

## Standard command

```bash
python3 tools/rgm_context_pack.py build \
  --root . \
  --task "<user task / bug description>" \
  --verify "<known verification command, repeatable>"
```

Optional flags:

```bash
--include path/to/file.py          # force-include a known entry file, repeatable
--verify "python3 -m pytest -q"    # name the verifier explicitly, repeatable
--max-files 20                     # cap the number of selected_files
--max-file-bytes 200000            # skip oversized files
--max-context-tokens 24000         # estimated token budget
--print-json                       # print the full JSON, for script consumption
```

## Output fields

The core fields of `context-pack.json`:

| Field | Purpose |
|---|---|
| `selected_files` | Files to read or prioritize on round one, with score, reason, and estimated tokens |
| `relevant_symbols` | Class / function / type / constant entry points pulled from the selected files |
| `test_commands` | Explicit or auto-detected verification commands: pytest, npm, go, cargo, make, and so on |
| `risk_files` | High-impact files such as config, auth, router, shared, payment |
| `unknowns` | Uncertainties you must resolve or record before entering PRECHECK |
| `token_budget` | Estimated token usage of the selected context |
| `scope_rule` | Out-of-bounds changes require a hypothesis and rationale in the ledger |
| `not_completion_proof` | Always true; keeps you from mistaking the context pack for a green light |

## Where it sits in the state machine

```text
INTAKE → CONTEXT_PACK → PRECHECK → BASELINE → ... → GATE → REPORT
```

- `INTAKE` decides whether this belongs in red-green mode at all.
- `CONTEXT_PACK` decides what to look at first, where the risk is, and what the unknowns are.
- `PRECHECK` is where you formally confirm the verifier, environment, permissions, and business-rule / PRD coverage.

Ledger example:

```bash
python3 tools/rgm_ledger.py add --out .rgm-ledger.json \
  --state CONTEXT_PACK \
  --command "python3 tools/rgm_context_pack.py build --root . --task '<task>' --verify 'python3 -m pytest -q'" \
  --result "selected_files=8 risk_files=2 unknowns=1" \
  --next PRECHECK
```

## Rules of use

1. **Run it by default on large or non-trivial code tasks**: bugfixes, cross-file changes, unfamiliar repos, and long runs should all produce a context pack.
2. **A one-off change you can see at a glance may skip it, with a note**: a single-file typo, say — but the report has to say why it was unambiguous.
3. **A non-empty `unknowns` is not a failure**: it's the PRECHECK to-do list. No verifier → TDD first; dirty worktree → record the boundary; over budget → slice it.
4. **Don't wander the whole repo**: read `selected_files` first and expand along symbols or imports only when you must; write the expansion into the ledger.
5. **Be extra careful with a dirty git tree**: the context pack writes the dirty state into unknowns; never overwrite the user's existing changes.
6. **Still run BASELINE afterwards**: the context pack doesn't replace the baseline and tells you nothing about the red/green count.

## Common misuse

- Misuse: `context-pack.json` was generated, so I can report done.
  - Reality: it only gets you into PRECHECK/BASELINE; done requires the verifier and `rgm_gate.py` PASS.
- Misuse: the file isn't in selected files, so it's absolutely off limits.
  - Reality: you can change it, but you must write the hypothesis and rationale into the ledger, and preferably rebuild or update the context pack.
- Misuse: no test command was detected, so I'll keep fixing by feel.
  - Reality: PRECHECK marks `BLOCKED_BY_INFRA` or switches to TDD first.
- Misuse: crank `--max-files` up to several hundred to be safe.
  - Reality: that recreates the context explosion. Start with a small pack and slice by module when you have to.

---

> Everything below was moved verbatim out of SKILL.md (the v0.19.0 slim-down; see the references index table in SKILL.md). Only its location changed, not a word of the text.

## Context Pack Gate: look at the right things before entering PRECHECK

The point of absorbing RepoPrompt CE isn't to use it as a green-light referee; it's to put it in front of the loop as **context engineering**. Before entering `PRECHECK`, generate a read-only context pack that tells the agent exactly which files, which symbols, which verification commands, and which risk files matter — and what's still unknown.

```bash
python3 tools/rgm_context_pack.py build \
  --root . \
  --task "<user task / bug description>" \
  --verify "<known verification command, repeatable>"
```

Default output:
- `.rgm/context-pack.json`: machine-readable fields, including `selected_files` / `relevant_symbols` / `test_commands` / `risk_files` / `unknowns` / `token_budget`.
- `.rgm/context-pack.md`: a human-readable summary for cold starts and handoffs.

Hard rules:
- The context pack is an **input artifact**, not proof of completion; `not_completion_proof=true` must stay. Done is still decided by the external verifier, `rgm_gate.py`, the ledger, and the contract.
- `selected_files` is your reading and editing boundary for round one. To change anything outside `selected_files` / `risk_files`, spell out the hypothesis and rationale in the Run Ledger.
- When `unknowns` is non-empty, resolve it in `PRECHECK` first: no verifier → switch to TDD; dirty worktree → record the boundary; oversized pack → slice it.
- Don't treat the context pack as an excuse to read the whole repo. Its purpose is the opposite: converge the context first, then run red-green.

Full how-to in `references/context-pack-gate.md`.

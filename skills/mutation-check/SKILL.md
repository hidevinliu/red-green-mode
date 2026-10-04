---
name: mutation-check
description: >-
  Lightweight mutation testing that answers one question: does this test actually bite the code it
  claims to guard? It breaks the target production code on purpose; the test must go red. If it
  stays green, it is a dead or weak test. Use when the user asks "is this test testing anything",
  "does this test have teeth", "find dead/weak tests", "run a mutation test", "are my acceptance
  criteria real", or "I changed the code and no test went red" (这个测试是不是白测了 / 有没有牙 /
  找死测试 / 帮我做 mutation test). Zero dependencies (Python stdlib). Python is first-class
  (function-level `file.py::func` targets); C-family languages are best-effort. Not for running a
  test suite, measuring coverage, or writing tests.
---

# mutation-check · find dead and weak tests

A standalone entry point to red-green-mode's contract quality gate. It reuses
`tools/rgm_mutation.py` from this repository, so you can hunt dead tests without entering the full
red-green loop.

**Requires** the red-green-mode repository on disk. In the commands below, `$RGM` is its root
(for example `~/.claude/skills/red-green-mode`). See "Install" at the end.

## What it catches

Beyond "green is not the same as correct" there is a third trap: **a test that is always green
proves nothing.** Common shapes:

- `assert True`, an assertion that always holds, or a test that only checks "no exception"
- a test that asserts on a mock's return value and never touches real logic
- a test that exercises function A while claiming to guard function B
- core logic changed and not one related test went red (a blind spot)

mutation-check injects a single-point change into the target code (flipped comparison, emptied
return, negated boolean, flipped arithmetic operator, number ±1, commented-out line) and re-runs
the test. **The test must go red.** If every sampled mutation survives, the test does not bite
that code.

## Usage

### A · Check one pair (most common)

Give it one test command and the production code it should guard (narrow it to a function with
`::func`):

```bash
python3 "$RGM/tools/rgm_mutation.py" check-pair \
  --verifier "python3 -m pytest tests/test_calc.py::test_add -q" \
  --target   "src/calc.py::add" \
  --root . --format sentinel
# ALIVE = has teeth (>=1 mutation killed); DEAD = dead/weak test
# exit 0 = ALIVE or N/A, 1 = DEAD, 2 = BLOCK
```

Before calling a test DEAD, narrow the target to the function the test **actually asserts on**
(`file.py::func`) and run again. Many DEAD results come from a target that is too large (a whole
file) where sampling missed the lines the test covers. That is a false DEAD, not a weak test.

### B · Check every item in an acceptance contract

If you already have an `acceptance-contract.json` (the shared source of truth for the
PRD → TDD → red-green handoff), check each criterion's verifier:

```bash
python3 "$RGM/tools/rgm_mutation.py" check \
  --contract acceptance-contract.json --root . --format sentinel
```

### Explain why one item is DEAD

```bash
python3 "$RGM/tools/rgm_mutation.py" explain \
  --contract acceptance-contract.json --item AC-03 --root .
# lists the mutations tried; for ALIVE, which one was killed; for DEAD, how many survived
```

## Reading the result

| Result | Meaning | Next step |
|---|---|---|
| **ALIVE** | The test goes red when the code breaks | The target is real |
| **DEAD** | Every sampled mutation survived | ① Narrow the target to `::func` to rule out a false DEAD. ② Still DEAD: the test checks the wrong thing or asserts too loosely. Strengthen the assertion |
| **N/A** | No target given, or the item guards nothing | Use `check --require-targets` to force a target on every item |
| **BLOCK** | Target or symbol not found, or a leftover backup exists | Fix the target path; clear leftovers with `restore --root .` |

## Safety and limits

- **Writes to your files, but leaves nothing behind.** Each mutation is preceded by a byte-level
  backup; a `finally` block restores it unconditionally, and an on-disk sidecar covers a killed
  process. After a normal run the working tree is byte-identical. Never commit mid-run.
- **Weak criterion by design.** It asserts that a test has *some* teeth (at least one mutation
  killed), not that its teeth are complete. Sampling is capped, so a huge target with a narrow test
  can produce a false DEAD; narrow with `::func`.
- **Languages.** Python is first-class (AST syntax check plus `::func`). C-family files
  (`.js/.ts/.go/.java/.rs`…) are best-effort: no `::symbol`, commenting a line can cut a multi-line
  statement, and it has not been verified end-to-end on real runtimes.
- Full limitations are in the docstring at the top of `tools/rgm_mutation.py`. The referee for the
  tool itself is `tests/test_mutation.py`.

## Install

```bash
git clone https://github.com/hidevinliu/red-green-mode ~/.claude/skills/red-green-mode
ln -s ~/.claude/skills/red-green-mode/skills/mutation-check ~/.claude/skills/mutation-check
export RGM=~/.claude/skills/red-green-mode
```

For Codex or another runtime, link the same folder into that runtime's skills directory.

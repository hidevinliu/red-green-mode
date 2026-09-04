# Red-Green · Minimality / Scope Review

> **When to read**: During `PRECHECK`, `PLAN_ONE_FIX`, or `QUALITY_REVIEW` when the
> task could invite a new abstraction, dependency, wrapper, refactor, or broad
> change. This is a soft design review inspired by YAGNI/stdlib-first practice.
> It is not a verifier, not a completion proof, and never overrides a required
> safety check or an explicit user requirement.

## Why this exists

An agent can reach green while still writing too much code: a new helper when an
existing one would do, a dependency for a standard-library operation, or a
speculative abstraction that is not needed by the current task. Red-green mode
already protects correctness and completion; this review reduces unnecessary
surface area **before** the patch is chosen.

The rule is **minimum correct change**, not minimum line count. A shorter patch
that drops validation, error handling, accessibility, observability, or a test
is not minimal; it is incomplete.

## `PRECHECK` / `PLAN_ONE_FIX`: minimality card

Before editing, write a compact card in the Run Ledger or plan:

```text
Need: why this change must exist now
Reuse: existing helper/pattern/dependency checked; result
Native: standard-library/platform option checked; result
Scope: files, symbols, and behavior allowed to change
Simplification: what is intentionally not being built
Ceiling: the known limit and the trigger to revisit it
```

Ask, in order:

1. Can the task be solved by configuration, an existing behavior, or no code?
2. Can an existing helper, pattern, standard-library function, native feature,
   or already-installed dependency cover it?
3. What is the smallest **correct** change that reaches the stated acceptance
   condition without widening the scope?
4. Is a new abstraction or dependency justified by a concrete second use,
   boundary, or measured constraint today?

Do not spend a research round proving that a one-line solution exists. Once the
first correct option is known, use it and move on.

## `QUALITY_REVIEW`: over-engineering pass

After the smoke assertion is green, inspect the final diff for these findings:

| Tag | Look for | Response |
|---|---|---|
| `delete` | dead, speculative, or unneeded code | remove only if the task remains covered |
| `stdlib` | hand-rolled standard-library behavior | replace with the library primitive |
| `native` | code duplicating a platform capability | use the native capability |
| `yagni` | an abstraction, config, or dependency with no current need | inline or defer it |
| `shrink` | a smaller implementation with the same behavior | simplify only after checking edges |

For each finding, choose one of: **fix now**, **record as deliberate debt**, or
**leave it because the larger shape is required**. If a finding affects
correctness, security, data loss, accessibility, or the user's explicit scope,
it is not a style suggestion: add a red-capable check or return to
`PLAN_ONE_FIX`.

## What this review may not do

- It must not impose a line-count target or fail a run because code is long.
- It must not delete tests, weaken assertions, remove validation, or hide errors
  to make a diff smaller.
- It must not turn maintainability judgment into a fake mechanical gate.
- It must not widen the task into a repo-wide cleanup.
- It must not report a saving number that was never measured. If efficiency is
  relevant, report changed lines, tool calls, tokens, cost, and verifier result
  separately.

## Wrap-up fields

Add these to the final report when the review was relevant:

```text
Minimality: kept / simplified / deferred
Findings: <tag + location, or none>
Deliberate debt: <ceiling + revisit trigger, or none>
```

The final completion state still comes only from the external verifier and
`RGM_GATE=PASS`.

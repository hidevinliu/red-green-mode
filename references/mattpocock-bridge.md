# Matt Pocock Bridge for Red-Green Mode

This reference describes how to use a curated Matt Pocock-style workflow as a **pre/post layer** around red-green-mode.

## Principle

Matt-style skills improve alignment and engineering discipline; they are not completion proof. Red-green-mode still owns:

- external verifier exit codes;
- Run Ledger;
- anti-cheat / constraints / acceptance contract gates;
- PRD coverage status;
- final `FULL_GREEN` / blocked / stopped reporting.

## Where it fits

| RGM phase | Matt-style input | Use it for | Never use it as |
|---|---|---|---|
| Before `INTAKE` | Grilling | resolve ambiguity before autonomous execution | a substitute for user-confirmed hard gates |
| `CONTEXT_PACK` | Domain modeling | vocabulary, ADRs, existing seams | completion proof |
| `PRECHECK` | PRD synthesis | acceptance criteria → verifier mapping | green status |
| `PRECHECK` / planning | Vertical slices | milestone contracts and dependency ordering | permission for parallel shared-tree writes |
| `validator=absent` | TDD | create red-capable tests | permission to weaken tests |
| Bug not reproduced | Diagnosis loop | minimal red-capable repro | hypothesis-only fix |
| `PLAN_ONE_FIX` | Codebase design | choose seam / deep module shape | scope expansion |
| `QUALITY_REVIEW` | Two-axis review | separate Standards vs Spec findings | overriding verifier/gate result |

## Recommended bridge packet

When a Matt-style PRD or issue is handed to red-green-mode, require:

```markdown
## Red-Green Handoff Packet

### Objective
<one verifiable goal>

### Acceptance coverage
| Criterion | Validator | Current status |
|---|---|---|

### Verification pipeline
1. `<targeted>`
2. `<lint/type/build>`
3. `<full test>`
4. `<semantic smoke>`

### Guardrails
- Do not weaken tests or snapshots.
- Do not change out-of-scope files without a ledger hypothesis.
- If spec and tests conflict, stop at TEST_QUESTION.
```

## Completion rule

Matt bridge makes RGM safer by making the target clearer. It does not change completion semantics:

```text
FULL_GREEN = verifiers green + semantic smoke + gate pass + PRD coverage pass + no anti-cheat violation
```

---

> Everything below is copied verbatim from SKILL.md (the v0.19.0 slim-down; see the references index table in SKILL.md). It only changed location — not a word of the text changed.

## Matt Pocock Bridge: requirement alignment → acceptance contract → red-green

When the task isn't a well-defined, already-verifiable fix but an "idea / feature / PRD / architecture change / large refactor", borrow the Matt Pocock-style pre/post layer to make the target concrete first, then enter red-green mode:

- **Grill before build**: before `INTAKE`, ask one question at a time to clear up any ambiguity that would change the implementation or the acceptance criteria; anything answerable from code or docs, look up yourself first.
- **Domain modeling**: write domain vocabulary, business definitions, and key ADRs into `CONTEXT.md` / `docs/adr/` for `CONTEXT_PACK` to use.
- **PRD → acceptance coverage**: map every PRD acceptance item to a verifier; anything uncovered goes to TDD first and may not enter the loop directly.
- **Vertical slices**: split a large task into end-to-end verifiable milestones; a green milestone is not a finished project — advance automatically via `next_milestone`.
- **Wide refactor exception (don't force a vertical slice)**: when one mechanical change (renaming a column, retyping a shared symbol) has a **blast radius** across the whole codebase and shatters a thousand call sites in a single edit, **no vertical slice can land green** — don't force it into a tracer bullet. Sequence it as **expand→migrate→contract**: **expand** first (the new form coexists with the old, nothing breaks) → **migrate call sites in batches** sized by blast radius (one blocked-by-expand ticket per batch; CI stays green batch after batch because the old form is still there) → finally **contract** (delete the old form once nothing calls it, blocked-by every migration batch). When even the batches can't stay green, put them on a shared integration branch and have them all block one final integrate-and-verify ticket; green is only promised there. A milestone contract (`scope: milestone-N/M`) expresses this dependency chain naturally.
- **Two-axis review**: during `QUALITY_REVIEW`, review Standards and Spec in separate columns, so "the code is clean" can't hide "the requirement wasn't built", and "the requirement was built" can't hide "a standard was broken".

The bridge layer produces **input artifacts** only (requirements, vocabulary, acceptance mapping, slices, reviews). It never replaces the verifier / ledger / `rgm_gate.py` / PRD coverage as the judge of completion. Full mapping: `references/mattpocock-bridge.md`.

## PRD Completion Gate

"All green" in red-green mode only proves that **nothing broke among the things that were written as tests**; it **cannot prove that every requirement in the PRD is built** — a requirement that made it into the PRD but never became a test slips right past a full board of green. This is the third trap, after "green ≠ correct": **green ≠ done (with respect to the spec)**. So **whenever a task comes with a PRD / spec / acceptance script / requirement list / TaskList**, you must pass this gate before finishing: walk the PRD item by item, and never equate "the tests are green" with "the PRD is built".

This is exactly what closes the loop on the greenfield three-leg relay: **the PRD sets the standard → TDD turns every acceptance item into a red test → red-green mode drives them green**. The PRD completion gate makes sure the second leg dropped nothing: every acceptance item needs a **green verifier** speaking for it.

**Subflow (mandatory whenever there's a PRD):**

```text
PRD_PARSE → COVERAGE_MAP → GAP_CHECK
```

| Substate | Exit condition | Where failure goes |
|---|---|---|
| `PRD_PARSE` | break the PRD's requirements/acceptance items/P0s into a per-item checklist (each item one decidable sentence: "what counts as done") | no decidable acceptance item → back to the PRD author/user for more, mark `TEST_QUESTION` |
| `COVERAGE_MAP` | map each acceptance item to **the verifier that covers it** (test/smoke/lint/build); done during PRECHECK | an acceptance item with no verifier covering it → write it as a red test first, per `test-driven-development` |
| `GAP_CHECK` | before finishing, judge each item: done / uncovered / missing | anything "missing" or "uncovered" → not allowed to report `FULL_GREEN` |

**Three states per acceptance item (mark each one before finishing):**

| State | Criterion | What to do |
|---|---|---|
| ✅ `done` | covered by a green verifier, and actually implemented | counts as complete |
| ⚠️ `uncovered` | the code is there, but no test/smoke covers it | **false-green risk** — green proves nothing about it; add a targeted test/smoke to pull it into the red-green loop, then judge |
| ❌ `missing` | required by the PRD, never built | not done; even with every test green you **cannot** report `FULL_GREEN` |

**Hard rule**: as long as the PRD still has a `missing` or `uncovered` item, **never report `FULL_GREEN` and never say "it's done"**. "All tests green" and "the whole PRD is built" are two different things — the first means the verifiers caught nothing, the second takes this gate's item-by-item check. If you stop on budget or a blocker while the PRD still has gaps, report the matching stop state (`STOPPED_NO_PROGRESS`, etc.) and list the unfinished items honestly in the "PRD coverage" section of the wrap-up report. Don't dress it up as complete.

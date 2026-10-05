# Guard probe · pre-registered specification

Committed before any held-out data is generated or scored. Everything below is fixed; any later
change is listed under "Deviations" at the bottom with its reason, and the numbers before and after
the change are both reported.

## Problem

`rgm_pointpatch.py` v0.22.0 caught 0 of 6 overfit patches that special-case the failing tests by a
*feature* of the input (`if len(arr) == 7: return [...]`). Random neighbours mostly keep that
feature, so the patch looks like it changed a whole region, which is what a real fix does.

## Mechanism

1. **Find added guards.** Parse the target function before and after the patch. Every `if` test (and
   conditional-expression test) in AFTER whose `ast.dump` does not occur in BEFORE is an added guard.
   Guards that compare the full argument tuple to literals (`(a, b) == (17, 0)`) are skipped: those
   are single points, already handled by the neighbour rule.
2. **Fill the guard's region.** Generate candidate argument lists, evaluate the guard expression on
   each one inside the child process (in the module's own namespace), and keep the ones where it is
   true. Candidates come from feature-preserving mutations of the test inputs (same length with
   different elements, same value modulo k, same first element, same prefix, values near a numeric
   boundary) and from plain neighbours. Budget: 400 candidates per guard; at most 3 guards per patch.
3. **Guarded-constant rule.** An added guard is a **guarded constant** when all of these hold:
   - (a) at least **M = 8** distinct candidates satisfy it, and AFTER returns normally on them;
   - (b) AFTER returns the **same value** (`repr` of the return value) on all of them;
   - (c) BEFORE does **not** return one single value on them (the function normally depends on the
     input there; if BEFORE was constant too, the guard may just be a refactor);
   - (d) at least one test input whose behaviour the patch changed satisfies the guard;
   - (e) the constant is not trivial (`None`, `True`, `False`, `0`, `1`, `-1`, `""`, `[]`, `()`,
     `{}`) and is not a literal that appears in the guard itself (a clamp `if x > 100: return 100`
     is a legitimate constant region).
4. **Verdict.** SUSPECT if the v0.22.0 neighbour rule says SUSPECT **or** any added guard is a guarded
   constant. Otherwise the v0.22.0 verdict stands. The report names which rule fired.

The constants M = 8, budget 400, max 3 guards, and the triviality list are fixed here.

## Evaluation

**Seen data (development):** the v0.22.0 corpus, 29 QuixBugs programs, 112 correct and 38 overfit
patches, including the 6 feature-keyed ones. It was used to *motivate* the design; its numbers are
reported but are not the evidence.

**Held-out data, generated after this file is committed, by templates written before scoring:**

- *H-overfit*: feature-keyed special cases using features **not** used in the seen corpus (which used
  `len(x) == k` and `x % 7 == r`): a value range `lo <= x <= hi`, `sum(x) == s`, `x[0] == v`,
  `max(x) == m`, `s.startswith(p)`, and the conjunction `len(x) == k and x[0] == v`. Built per
  program only where the feature separates the failing tests from the passing ones, as before.
- *H-legit*: correct programs plus a guard that returns a constant the reference *really* returns on
  that whole region, found by search (for example an early return for inputs where the answer is
  provably fixed). These are legitimate patches built to look like the attack; they measure the
  guard rule's false positives where it is most likely to be wrong.

Labels on both come from the reference implementation exactly as in v0.22.0 (`common.label`).

**What gets reported, whatever it shows:**
- H-overfit caught / total, per feature.
- H-legit flagged / total.
- The seen corpus re-scored: correct flagged / 112, overfit caught / 38 (including the 6).
- How often the guard search fails to find M region inputs (reported as "region not reached").

**Acceptance for shipping:** the full test suite passes; the rule does not flag any of the 112 seen
correct patches *or* the result is published with that regression stated. No numeric catch-rate
target is set in advance; the number is whatever the held-out set gives.

## Not in scope

- Guards that call other functions of the module, use loops or depend on state.
- Constraint solving with an external solver (Z3 or similar). Region filling is search-based.
- Languages other than Python.

## Deviations

None. The held-out run used exactly the rule and constants above. Results: [`README.md`](README.md).

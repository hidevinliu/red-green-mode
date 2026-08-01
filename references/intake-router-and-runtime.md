# Red-Green Mode · Intake Router / Skill Matcher / Runtime Router

> This page turns the "semantic router → expert matcher → execution router" pattern into an executable layer inside red-green-mode. It is a **front-end routing layer**: it replaces neither the red-green core state machine nor the external verifier's ruling.

## 1. Intake Router: decide whether this task belongs in red-green mode at all

Entry command (pure stdlib; runs under both Claude Code and Codex):

```bash
python3 tools/rgm_intake.py --runtime claude-code --validator unknown --prompt "<user task>"
python3 tools/rgm_intake.py --runtime codex --validator present --prompt "<user task>"
```

Output fields:

| Field | Meaning |
|---|---|
| `route_name` | `red_green` / `tdd_first` / `systematic_debugging` / `research` / `doc_only` / `simple_or_discussion`, and so on |
| `should_enter_red_green` | Whether the red-green core loop may start; when `false`, hand off to a neighboring skill per `route_name` or just answer normally |
| `recommended_mode` | `single_agent` / `longrun` / `parallel_candidate_requires_decompose_gate` |
| `auxiliary_skills` | Capabilities worth pairing in, such as `test-driven-development` / `systematic-debugging` / `mutation-check` / `web-evidence` |
| `required_gates` | Gates to switch on: `business_rule_discovery` / `prd_completion` / `acceptance_contract` / `rgm_gate` |
| `runtime_policy` | The enforcement gap between Claude Code and Codex: hard vs soft |

**Ironclad rules:**
- When `should_enter_red_green=false`, do not force your way into red-green mode; route to TDD, systematic debugging, research, or doc-only instead.
- When `validator=absent` or there is no trustworthy verifier, default to `tdd_first`: build the acceptance tests first, then come back to red-green mode.
- `parallel_candidate_requires_decompose_gate` is not permission to parallelize — only a candidate. You still have to run `rgm_partition.py check`.

## 2. Skill Matcher: red-green mode is not a universal hammer

After routing, load the minimum necessary capabilities:

| Signal | Auxiliary capability |
|---|---|
| No tests / acceptance criteria not yet formed | `test-driven-development` |
| Complex root cause; unclear whether several reds share one | `systematic-debugging` |
| Weak tests / false green / dead target | `mutation-check` |
| External facts, policies, API behavior | Web evidence — freeze findings into local assertions afterward |
| PRD / acceptance script / task list | PRD Completion Gate + acceptance contract |

Load only the 1-2 most relevant capabilities; piling on more turns the context into prompt soup. Capability selection can only help you localize the problem or build a judge — it never replaces the final ruling from `rgm_gate.py`.

## 3. Runtime Router: isomorphic degradation across Claude Code and Codex

| Runtime | Capability | Requirement |
|---|---|---|
| Claude Code | Supports a Stop hook, so a FAILing gate can physically block finishing | You still have to run `rgm_gate.py` yourself; the hook is a backstop, not an exemption |
| Codex / CodexAI | No Stop hook; pure-Python tools, ledger, gate, and mutation all still run | Soft-enforcement discipline: only `RGM_GATE=PASS` allows reporting `FULL_GREEN`; after a cold start, run `rgm_ledger.py bootstrap` first |

**Shared source of truth**: under any runtime, the real state is `.rgm-ledger.json` plus `PROGRESS.md` (during long runs). Don't rely on model memory, and don't treat `.contextwarden/` as a second parallel system.

## 4. Memory / index layer: what to write and what not to

| Content | Location |
|---|---|
| Per-round state, red count, hypotheses, commands | `.rgm-ledger.json` |
| Long-run handoff, next step, human-readable summary | `PROGRESS.md` |
| Business or web-sourced evidence | Evidence Ledger |
| PRD coverage mapping | `acceptance-contract.json` + the ledger's final entry |
| Large logs / worker output | `logs/` / `worker-reports/` |
| Reusable project experience | The project's own red-green memory note (optional) |

Don't write every action into long-term memory. Long-term memory keeps only reusable facts: the verifier map, common `ENV_RED` causes, the flaky list, project conventions, and runtime differences.

## 5. Minimal entry flow

```text
INTAKE(rgm_intake.py)
  ├─ should_enter_red_green=false → route to TDD/debug/research/doc-only
  └─ should_enter_red_green=true
       ↓
     PRECHECK(confirm a real verifier, the ledger, the workspace, the gates)
       ↓
     red-green core state machine
       ↓
     GATE(rgm_gate.py; soft enforcement on Codex, optional hard enforcement on Claude Code)
       ↓
     REPORT(includes the intake snapshot + runtime policy)
```

---

> The section below is copied verbatim from SKILL.md (v0.19.0 slimming pass — see the references index table in SKILL.md). It only moved; not a word of the body changed.

## Intake Router: route first, then enter red-green mode

The "semantic router → expert matcher → execution router" pattern lands in this skill as a **cross-runtime front-end routing layer**. Whenever a task carries any ambiguity — should this be red-green at all, is there a verifier, should it be a long run or parallel, should TDD/debug/research come first — run this first:

```bash
python3 tools/rgm_intake.py --runtime claude-code --validator unknown --prompt "<user task>"
python3 tools/rgm_intake.py --runtime codex --validator unknown --prompt "<user task>"
```

`rgm_intake.py` only does deterministic routing; it never declares completion. It outputs `route_name`, `should_enter_red_green`, `recommended_mode`, `auxiliary_skills`, `required_gates`, and `runtime_policy`.

Ironclad rules:
- `should_enter_red_green=false` → **do not force your way into red-green mode**; route by `route_name` to `test-driven-development`, `systematic-debugging`, research, or doc-only.
- `validator=absent` or no trustworthy verifier → go `tdd_first`: turn the acceptance criteria into tests, then come back to red-green mode.
- `parallel_candidate_requires_decompose_gate` is a candidate, not permission; you still have to run `rgm_partition.py check`.
- Claude Code can block physically with a Stop hook; CodexAI/Codex has no hard Stop hook, so it relies on **soft-enforcement discipline**: run `rgm_gate.py` yourself, and report `FULL_GREEN` only on `RGM_GATE=PASS`.

Full how-to in `references/intake-router-and-runtime.md`.

# Red-green · What this mode does / theory foundations / preconditions before you start (PRECHECK)

> **When to read this**: when you want to know why red-green is designed the way it is, or exactly which preconditions to confirm before entering the loop. SKILL.md keeps only the skeleton; this page holds the original prose.

> The whole section below was moved verbatim out of SKILL.md (the v0.19.0 slim-down, see the references index table in SKILL.md); only its location changed, not a word of the text.

> 📌 **`VERSION-AND-STATE.md` is the single source of truth for state files and stop-condition ownership** (which of `.rgm-ledger.json`/`PROGRESS.md`/`.contextwarden` is which, and which loop layer governs which). Which capabilities shipped is in `CHANGELOG.md`; if a version number here conflicts with it, `CHANGELOG.md` wins.

## What this mode does

It puts you inside an **unattended self-verifying loop**: all verifiers green means you're clear to finish; any single red means you stop and fix that specific thing, then verify again. The core belief:

> **Right and wrong are decided by the verifier's exit code, not by the agent.** "I think it's fine" doesn't count; a green test does.

This is the fundamental reason autonomous coding can run unattended for a long time — **an objective judge plus no ambiguous decision points**, so nothing along the way needs a human.

**This mode only handles the outer orchestration** — when to stop, what counts as done, no cheating allowed, whether a way back exists; it **does not teach you how to diagnose a red light**. Actually investigating a red light belongs to `systematic-debugging` (systematic root-cause location); missing acceptance criteria (no tests at all) belongs to `test-driven-development`, which stands the tests up first. Red-green is the judge and the guardrail; diagnosis and test-writing are two separate jobs — don't rebuild them inside this skill.

## Why it's designed this way (theory foundations)

This mode isn't a pile of anecdotes. At its core is a principle with a formal name — **the asymmetry of verification (Verifier's Law, Jason Wei)**: **verifying a solution is usually far cheaper than generating one from scratch**; almost any task that can be verified cheaply and objectively can be automatically optimized to a high standard. So what red-green does is **reduce the open-ended problem of "write correct code" to repeated search against a verifiable target** — and the exit code is that target. This is also why each of the guardrails below is non-negotiable:

- **The judge must be external, never the agent itself**: research keeps finding a *generation-verification gap* in LLMs (the correct solution is in the samples, but the model can't pick it out) along with a bias toward their own output; in a long loop, errors compound across rounds and blow up "at step 37". → So **exit codes** decide, not "I think it's fine".
- **Faking green is forbidden (ironclad rule 1)**: an agent optimizes for "done", not "correct", and will happily delete or weaken tests to make the suite "pass" (Kent Beck calls them *unpredictable genies*). Tests are "external truth that doesn't drift no matter how long you run", **but only if the tests themselves are protected from the agent** — that's what a hook guarding test files and protected config is for.
- **You need a stop budget and a way back (ironclad rules 2 and 3)**: any error-correction loop can turn into an infinite loop or a money pit; an external trigger plus a hard budget (primarily the no-progress exit code, rounds as a secondary signal, wall clock optional) plus revertible checkpoints is standard production equipment.

> Same-family ecosystem: the Ralph loop (Geoffrey Huntley), Codex `/goal`, and RLVR (reinforcement learning with verifiable rewards, the training paradigm behind modern coding models) are all different packagings of the same principle.

## First thing after triggering: confirm the preconditions

`INTAKE` comes first, then `CONTEXT_PACK`, and only then `PRECHECK`. If `rgm_intake.py` has already returned `should_enter_red_green=false`, don't force your way through here; only when routing allows entry into red-green do you generate the context pack and run the precondition checks below. An unattended loop is only trustworthy when all three preconditions hold — if one is missing, don't force it:

1. **An objective verifier exists** (at least one of tests / compilation / type checking / lint can run and can call it right or wrong).
   - No tests, or the bug hasn't been reproduced by any command yet → **don't just say "switch to TDD" and call it done** — first pass the **red-signal construction gate** below and treat "produce a red light you can fix against" as the highest-leverage step available right now. "Self-verification" without a judge is fake, and the agent will only reassure itself.
2. **The judge itself is trustworthy** — don't blindly trust an exit code (see the generation-verification gap above). Before you start, confirm:
   - **The red is a real red**: reproduce it once with the same command and confirm it isn't a false red from a dirty environment, a missing dependency, or a leftover artifact, then touch the code. Frantically editing code against environmental noise is the classic waste.
   - **The green can catch errors**: if you changed core logic and it went green without a single related test moving, suspect a test blind spot (a false green). **Green ≠ correct; green only = the existing tests didn't catch it.**
   - **The green isn't vacuously true**: watch out for *tautological* tests — assertions that recompute the expected value **the same way the code under test does** (`expect(add(a,b)).toBe(a+b)`, snapshots generated by hand with the same method, a constant asserted equal to itself). They pass by construction, can never disagree with the code, and catch no bugs. Expected values must come from an **independent source of truth**: known-good literals, hand-computed examples, or the spec.
3. **The task boundary is clear and unambiguous** (the goal can be stated as "make X all green", with no "which way should this go?" decisions to ask a human about mid-run).
   - If ambiguous decision points exist → settle them with the user first, or have the agent **collect them into a list** for after-the-fact approval, rather than stalling mid-run.

Four more things to prepare before entering the loop:

- **Confirm the verification commands are already on the permission allowlist** (otherwise every test run pops a confirmation, the loop gets interrupted, and unattended is off the table). List the commands you're going to use for the user.
- **Record the runtime environment and the judge's entry point**: at minimum record `pwd`, the verification commands, and the interpreter/runner path and version (e.g. `which python`, `python --version`, `which pytest`, `node --version`). When the test runner and the smoke/runtime environment disagree, run under the environment the project declares (`uv.lock` / `.python-version` / `package.json` / `.nvmrc`) so you don't let a "tests green, actual run red" false green slip through.
- **Confirm the workspace is clean and note the starting point** (`git status` clean; record `git rev-parse HEAD` or `git stash` a starting point). An autonomous loop with no way back only degrades in one direction once it drifts — see ironclad rule 3.
- **[Mandatory] Create the ledger before you start and record the wall-clock start**: every time you enter the red-green loop, **step one is running `rgm_ledger.py init`** (it writes `started_at` automatically) — **create it regardless of task size, even for a single round**, so that at the end you can always compute elapsed time instead of "the task was small so I forgot to report it". **The only exemption**: when you genuinely lack write permission, note a start timestamp yourself (run a `date` for the record) and compute elapsed time by hand at the end. Elapsed time is a **cost metric, not a completion criterion** — don't conflate "fast or slow" with "green or not" (completion is decided by the verifier's exit code, as below).
- **Decide whether the business rule discovery gate applies**: when money, people, contracts, SOPs, operational templates, or spreadsheet formulas are involved, run the Business Rule Discovery Gate below first.
- **Decide whether a PRD / acceptance scenario / requirements list / TaskList exists**: if one does, run the PRD completeness gate below first — extract every acceptance item into a checklist and map it to a verifier, and any acceptance item no test covers has to be stood up as a red test via TDD first. Otherwise "all green" only proves that the parts already written as tests didn't break; it **can't prove the whole PRD is done**.

---

## Appendix: full text of the frontmatter `description` before the v0.19.0 slim-down (archived)

> To keep resident context cheap, `SKILL.md`'s description has been squeezed under 500 characters. The original is archived here without changing a word, so any trigger phrase that got dropped can be restored from here.

> Archived verbatim: the original Chinese frontmatter description, kept as a trigger-keyword backup.

```yaml
description: >-
  自主"测试-修复"循环模式(红绿灯模式 / Red-Green Mode)。让 agent 在开发或修 bug 时进入
  edit→verify→edit 的无人值守闭环:反复跑验证器(lint / 类型检查 / 编译 / 单元 / 集成测试 /
  CI 流水线,任何语言栈——pytest / jest / go test / cargo / tsc / ruff / eslint 等),红灯就
  针对性修、修完再验,直到全绿才收工——对错由验证器退出码裁决,不靠 agent 自我感觉。
  当用户说「红绿灯模式」「进入红绿灯」「自己跑到绿」「绿了才算完」「自动测试修复」「跑到测试全过」
  「self-verify」「red-green」「让它自己修到没问题」,或要求"你自己改自己跑、红了接着修、绿了才算完、
  把 CI 修到全绿、不用我盯着 / 我去开会回来要看到绿"——这类把"什么算完成"交给测试套件裁决、要
  agent 无人值守长跑(含开发新工具/脚本时自己补测试、自己编译、自己 lint 到全绿)的场景,必须使用本 skill。
  也涵盖「长跑模式」「跑很久不爆上下文」「上下文要爆/快满了」「检查点续跑/cold-start」「多 Agent/并行红绿灯」等无人值守超长跑或可分任务并行的场景；并内置 Intake Router、Context Pack Gate、Content-addressed CodeMap/上下文选择、first-party live MCP repo 工具边界、外部 MCP adapter registry/host preflight 与 write-gated MCP patch 工具，先判断是否该进入红绿灯、再把相关文件/符号/依赖/验证命令/风险/未知数打成上下文包，并按单写者 Agent orchestration 在 Claude Code 与 CodexAI/Codex 都同构降级到可验证 gate。
  不适用于:没有客观验证器的任务(整理笔记 / 写文档 / 润色等主观文案——没有"绿灯"可言);一次性、
  不需迭代的简单改动(改个 typo、只跑一次测试看结果)。也要和相邻场景区分:从零写测试立验收标准用
  test-driven-development;只查单个 bug 根因用 systematic-debugging;只 review/解释代码、或只写
  CI/CD 配置本身(而不是让 agent 把跑红的修到绿),都不触发本 skill。
```

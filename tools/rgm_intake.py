#!/usr/bin/env python3
"""Deterministic intake router for red-green-mode.

This is the cross-runtime front door: Claude Code and Codex can both run this
stdlib-only CLI before PRECHECK to decide whether a task should enter red-green,
be routed to TDD/systematic debugging/research/doc-only, and which gates/modes
are required. It is intentionally conservative: ambiguous or validator-less
requests should not be forced into an autonomous red-green loop.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from typing import Iterable

RED_GREEN_TERMS = [
    r"红绿灯", r"red[- ]?green", r"self[- ]?verify", r"跑到绿", r"全绿", r"绿了才算",
    r"测试全过", r"CI\s*(?:全)?绿", r"修到(?:测试)?(?:全)?过", r"fix .*tests?", r"until .*pass",
    r"不用我盯", r"无人值守", r"自动测试修复", r"自己改自己跑",
]
TDD_TERMS = [r"没有测试", r"没(?:有)?验证器", r"先写测试", r"TDD", r"验收标准", r"立(?:一个)?红测试"]
DEBUG_TERMS = [r"定位", r"根因", r"为什么.*失败", r"debug", r"systematic", r"查.*bug", r"排查"]
RESEARCH_TERMS = [r"调研", r"研究", r"联网", r"搜索", r"最新", r"官方文档", r"政策", r"法规", r"API 文档"]
DOC_ONLY_TERMS = [r"整理笔记", r"润色", r"改文案", r"写文章", r"总结", r"翻译", r"只改文档", r"文档说明"]
LONGRUN_TERMS = [r"长跑", r"跑很久", r"不爆上下文", r"上下文.*(?:满|爆)", r"检查点续跑", r"cold[- ]?start"]
PARALLEL_TERMS = [r"并行", r"多\s*Agent", r"multi[- ]?agent", r"worktree", r"分任务"]
PRD_TERMS = [r"PRD", r"\bspecs?\b", r"需求清单", r"验收", r"acceptance", r"TaskList", r"里程碑"]
BUSINESS_TERMS = [r"财务", r"薪资", r"HR", r"合同", r"账单", r"发票", r"SOP", r"客服规则", r"权限矩阵", r"表格公式", r"对外交付", r"金额", r"客户承诺"]
MUTATION_TERMS = [r"弱测试", r"假绿", r"mutation", r"变异测试", r"死靶", r"测试没咬住"]
VALIDATOR_TERMS = [r"pytest", r"jest", r"vitest", r"go test", r"cargo test", r"\btsc\b", r"\blint", r"\bCI\b", r"测试", r"编译", r"类型检查"]
# C1: cheat intent = tamper with the tests in order to force a green. Detected as a TRIPLE
# co-occurrence — a tamper VERB acting on a TEST object for a GREEN goal — so legit work
# (delete an obsolete test / fix a skipped test / skip slow tests) that lacks the green-goal
# OR the tamper-verb is NOT caught. Plus a few DIRECT fabrication phrases that stand alone.
# ⚠️ HEURISTIC and NON-EXHAUSTIVE: reworded intent escapes and rare legit phrasings can trip
# it; this is a soft front-door signal, NOT a hard boundary. The real defense against cheating
# is the downstream anticheat gate scanning the actual diff (see ANTICHEAT-LIMITATIONS).
_CHEAT_VERB = [r"注释掉?", r"删掉?", r"删除", r"去掉", r"mock\s*掉", r"禁用", r"屏蔽",
               r"comment\s*out", r"\bdelete\b", r"\bremove\b", r"\bdisable\b", r"stub\s*out"]
_TEST_OBJ = [r"测试", r"断言", r"assert", r"用例", r"\btest", r"\bci\b", r"\bcheck"]
_GREEN_GOAL = [r"变绿", r"标绿", r"跑绿", r"让.{0,3}过", r"通过", r"收工", r"算完",
               r"turns?\s*green", r"make.{0,6}(pass|green)", r"to\s*pass"]
_DIRECT_CHEAT = [
    r"直接标绿", r"标记?为绿", r"强行.{0,4}(标绿|变绿|通过)",
    r"(显示|弄成|改成).{0,6}绿.{0,10}(就行|即可|不用|别管)",
    r"假装.{0,6}(通过|绿|pass|green)",
    r"绕过.{0,6}(测试|验证|检查|gate|裁判)",
    r"跳过测试.{0,8}(标绿|收工|通过|算完|就行)",
    r"不(?:真正?|用|想)?(?:改代码|真修|修).{0,8}(标绿|通过|变绿|绿|过)",
    r"fake.{0,8}(pass|green)", r"force.{0,8}green", r"bypass.{0,12}(test|verif|check|gate)",
]


def _cheat_intent(text: str) -> bool:
    """True when the prompt shows intent to cheat the tests green. Heuristic — see note above."""
    if has_any(text, _DIRECT_CHEAT):
        return True
    return (has_any(text, _CHEAT_VERB) and has_any(text, _TEST_OBJ)
            and has_any(text, _GREEN_GOAL))


@dataclass
class IntakeResult:
    route_name: str
    should_enter_red_green: bool
    confidence: float
    recommended_mode: str
    auxiliary_skills: list[str]
    required_gates: list[str]
    validator_status: str
    reason: str
    runtime_policy: dict[str, str]


def has_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


def classify(prompt: str, validator: str = "unknown", runtime: str = "unknown") -> IntakeResult:
    text = " ".join(prompt.split())
    lower_validator = validator.lower()
    if lower_validator not in {"present", "absent", "unknown"}:
        lower_validator = "unknown"

    wants_rg = has_any(text, RED_GREEN_TERMS)
    wants_tdd = has_any(text, TDD_TERMS)
    wants_debug = has_any(text, DEBUG_TERMS)
    wants_research = has_any(text, RESEARCH_TERMS)
    doc_only = has_any(text, DOC_ONLY_TERMS) and not wants_rg
    has_validator_hint = has_any(text, VALIDATOR_TERMS) or lower_validator == "present"

    required_gates: list[str] = []
    if has_any(text, BUSINESS_TERMS):
        required_gates.append("business_rule_discovery")
    if has_any(text, PRD_TERMS):
        required_gates.append("prd_completion")
    if required_gates:
        required_gates.append("acceptance_contract")
    required_gates.append("rgm_gate")

    auxiliary: list[str] = []
    if wants_tdd or lower_validator == "absent":
        auxiliary.append("test-driven-development")
    if wants_debug:
        auxiliary.append("systematic-debugging")
    if wants_research:
        auxiliary.append("web-evidence")
    if has_any(text, MUTATION_TERMS):
        auxiliary.append("mutation-check")

    mode = "single_agent"
    if has_any(text, PARALLEL_TERMS):
        mode = "parallel_candidate_requires_decompose_gate"
    elif has_any(text, LONGRUN_TERMS):
        mode = "longrun"

    if _cheat_intent(text):
        route = "cheat_intent_refused"
        enter = False
        confidence = 0.75  # heuristic, not exhaustive — deliberately not high-confidence
        reason = ("Prompt matches a common cheat ACTION (tamper with tests to force a green). "
                  "Heuristic keyword match, NOT exhaustive — reworded intent can escape, so this "
                  "is a soft front-door signal, not a hard boundary; the real defense is the "
                  "downstream anticheat gate scanning the actual diff.")
    elif doc_only:
        route = "doc_only"
        enter = False
        confidence = 0.82
        reason = "Task appears subjective/document-only; no objective red-green validator is implied."
    elif wants_tdd or lower_validator == "absent":
        route = "tdd_first"
        enter = False
        confidence = 0.88 if wants_tdd else 0.78
        reason = "No trustworthy objective validator yet; establish acceptance tests before red-green."
    elif wants_rg and (has_validator_hint or lower_validator == "unknown"):
        route = "red_green"
        enter = True
        confidence = 0.95 if has_validator_hint else 0.86
        reason = "User intent delegates completion to external validators; enter red-green after PRECHECK confirms them."
    elif wants_debug and not wants_rg:
        route = "systematic_debugging"
        enter = False
        confidence = 0.76
        reason = "User asks for root-cause investigation, not an autonomous run-until-green loop."
    elif wants_research and not wants_rg:
        route = "research"
        enter = False
        confidence = 0.74
        reason = "Task primarily needs evidence gathering; only enter red-green after evidence is frozen into local assertions."
    elif has_validator_hint:
        route = "red_green_candidate"
        enter = True
        confidence = 0.68
        reason = "Objective validators are present; red-green is allowed if the user's target is to make them green."
    else:
        route = "simple_or_discussion"
        enter = False
        confidence = 0.62
        reason = "No clear autonomous test-fix loop; answer or clarify rather than forcing red-green."

    # Keep rgm_gate only for routes that may enter the red-green core *now*.
    # For tdd_first/no-validator/doc/research routes, the next action is to build
    # evidence or tests; run intake again after a trustworthy validator exists.
    if not enter and "rgm_gate" in required_gates:
        required_gates = [g for g in required_gates if g != "rgm_gate"]

    runtime_key = runtime.lower().replace(" ", "-")
    if runtime_key in {"claude", "claude-code", "cc"}:
        runtime_policy = {
            "runtime": "claude-code",
            "gate_enforcement": "hard stop-hook available if installed; still run rgm_gate before FULL_GREEN",
            "state": ".rgm-ledger.json + PROGRESS.md for longrun",
        }
    elif runtime_key in {"codex", "codexai", "codex-ai"}:
        runtime_policy = {
            "runtime": "codex",
            "gate_enforcement": "soft discipline: run rgm_gate and require RGM_GATE=PASS before FULL_GREEN; no Stop hook",
            "state": ".rgm-ledger.json + PROGRESS.md; bootstrap after compaction/cold-start",
        }
    else:
        runtime_policy = {
            "runtime": "unknown",
            "gate_enforcement": "portable Python gates still apply; hard hooks depend on runtime",
            "state": ".rgm-ledger.json + PROGRESS.md when needed",
        }

    # Deterministic de-dupe while preserving order.
    auxiliary = list(dict.fromkeys(auxiliary))
    required_gates = list(dict.fromkeys(required_gates))
    return IntakeResult(route, enter, confidence, mode, auxiliary, required_gates, lower_validator, reason, runtime_policy)


def main() -> int:
    parser = argparse.ArgumentParser(description="red-green-mode intake router")
    parser.add_argument("--prompt", default=None,
                        help="task prompt; if omitted entirely (not just empty), read stdin")
    parser.add_argument("--validator", choices=["present", "absent", "unknown"], default="unknown")
    parser.add_argument("--runtime", default="unknown", help="claude-code | codex | unknown")
    parser.add_argument("--format", choices=["json", "sentinel"], default="json")
    args = parser.parse_args()

    # D4: honor an explicit empty --prompt "" as empty; only fall back to stdin when
    # --prompt was omitted entirely, and never block on an interactive tty.
    if args.prompt is not None:
        prompt = args.prompt
    elif not sys.stdin.isatty():
        prompt = sys.stdin.read()
    else:
        print("error: provide --prompt (it may be empty) or pipe a prompt via stdin", file=sys.stderr)
        return 2
    result = classify(prompt, validator=args.validator, runtime=args.runtime)
    data = asdict(result)
    if args.format == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print("<!--RGM_INTAKE_BEGIN-->")
        print(f"ROUTE={result.route_name}")
        print(f"ENTER_RED_GREEN={'yes' if result.should_enter_red_green else 'no'}")
        print(f"CONFIDENCE={result.confidence:.2f}")
        print(f"MODE={result.recommended_mode}")
        print(f"AUXILIARY_SKILLS={','.join(result.auxiliary_skills) or 'none'}")
        print(f"REQUIRED_GATES={','.join(result.required_gates) or 'none'}")
        print(f"RUNTIME={result.runtime_policy['runtime']}")
        print("<!--RGM_INTAKE_END-->")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Tests for the cross-runtime red-green intake router."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def run_intake(prompt: str, *args: str) -> dict:
    proc = subprocess.run(
        [sys.executable, str(TOOLS / "rgm_intake.py"), "--prompt", prompt, *args],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_explicit_red_green_with_validator_enters_loop():
    data = run_intake("进入红绿灯模式，把 pytest 跑到全绿，绿了才算完", "--validator", "present")
    assert data["route_name"] == "red_green"
    assert data["should_enter_red_green"] is True
    assert data["recommended_mode"] == "single_agent"
    assert "rgm_gate" in data["required_gates"]


def test_missing_validator_routes_to_tdd_first():
    data = run_intake("这个项目没有测试，先帮我把验收标准立起来再修", "--validator", "absent")
    assert data["route_name"] == "tdd_first"
    assert data["should_enter_red_green"] is False
    assert "test-driven-development" in data["auxiliary_skills"]
    assert "rgm_gate" not in data["required_gates"]


def test_doc_only_does_not_force_red_green():
    data = run_intake("帮我整理笔记并润色这段文案")
    assert data["route_name"] == "doc_only"
    assert data["should_enter_red_green"] is False
    assert "rgm_gate" not in data["required_gates"]


def test_business_prd_prompt_requires_extra_gates():
    data = run_intake("根据 PRD 修薪资表格公式，跑到测试全过", "--validator", "present")
    assert data["should_enter_red_green"] is True
    assert "business_rule_discovery" in data["required_gates"]
    assert "prd_completion" in data["required_gates"]
    assert "acceptance_contract" in data["required_gates"]


def test_runtime_policy_differs_for_codex_and_claude_code():
    codex = run_intake("修到 CI 全绿", "--validator", "present", "--runtime", "codex")
    claude = run_intake("修到 CI 全绿", "--validator", "present", "--runtime", "claude-code")
    assert codex["runtime_policy"]["runtime"] == "codex"
    assert "soft discipline" in codex["runtime_policy"]["gate_enforcement"]
    assert claude["runtime_policy"]["runtime"] == "claude-code"
    assert "hard stop-hook" in claude["runtime_policy"]["gate_enforcement"]


def test_longrun_and_parallel_modes_are_explicit_candidates():
    longrun = run_intake("长跑模式，跑很久也不要爆上下文，修到测试全过", "--validator", "present")
    parallel = run_intake("多 Agent 并行红绿灯，用 worktree 分任务", "--validator", "present")
    assert longrun["recommended_mode"] == "longrun"
    assert parallel["recommended_mode"] == "parallel_candidate_requires_decompose_gate"


def test_intake_refuses_cheat_intent():
    """C1: a prompt whose intent is to bypass/fake the tests ('跳过测试直接标绿' — "skip the
    tests and just mark it green") must NOT get a red-green entry ticket. Ironclad Rule 1
    (no cheating to green) is enforced at the front door, not left to the downstream gate.
    The old candidate branch handed it enter=True merely because the word '测试' (test)
    appeared."""
    data = run_intake("跳过测试直接标绿，不用真修")
    assert data["should_enter_red_green"] is False
    assert data["route_name"] == "cheat_intent_refused"


def test_intake_legit_fix_skipped_test_still_allowed():
    """C1 guard (no false positive): 'fix the skipped test so it really passes' is legit
    work, NOT cheat intent — it must NOT be caught by the cheat filter."""
    data = run_intake("这个测试之前被 skip 了，帮我把它修好让它真的通过", "--validator", "present")
    assert data["route_name"] != "cheat_intent_refused"
    assert data["should_enter_red_green"] is True


def test_intake_empty_prompt_ignores_stdin_no_hang():
    """D4: an explicit `--prompt ""` must be honored as empty and classified immediately,
    NOT fall through to a blocking stdin read. Feed stdin a red-green trigger; the empty
    prompt must win — proving stdin was not consumed."""
    r = subprocess.run(
        [sys.executable, str(TOOLS / "rgm_intake.py"), "--prompt", ""],
        input="把 pytest 跑到全绿 红绿灯 无人值守", capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["should_enter_red_green"] is False, "empty --prompt must not read stdin"


# --- C2: word-bounded English validator terms (no substring false hits) ----

@pytest.mark.parametrize("prompt", [
    "decide which framework to adopt",              # de-CI-de
    "inspect the specification document carefully",  # spe-CI-fication + spec
    "please reconcile the two accounts",             # recon-CI-le
])
def test_intake_word_boundary_no_false_validator(prompt):
    """C2: short validator terms (CI/tsc/lint) and 'spec' must be word-bounded so they do
    NOT match inside decide / specification / reconcile and drag the task into red-green."""
    assert run_intake(prompt)["should_enter_red_green"] is False, prompt


def test_intake_real_ci_token_still_matches():
    """C2 guard: a real standalone 'CI' must still register as a validator hint."""
    assert run_intake("修到 CI 全绿", "--validator", "present")["should_enter_red_green"] is True


# --- C1: cheat-intent is a HEURISTIC — quantify coverage, don't pretend 100% ---
# These pin a coverage floor (regression catcher) and a no-false-positive guard. The
# residual (reworded cheating) is explicitly NOT claimed caught here; the real defense is
# the downstream anticheat gate scanning the actual diff.
CHEAT_SAMPLES = [
    "把失败的测试注释掉让它变绿",
    "让 CI 显示绿色就行不用真修",
    "mock 掉那段逻辑让测试过",
    "删掉这个测试然后跑绿",
    "直接标绿",
    "绕过测试直接收工",
    "comment out the failing test to make CI pass",
    "just delete the test so it turns green",
]
LEGIT_SAMPLES = [  # contain trigger words but are legitimate — must NOT be blocked as cheating
    "帮我修好这个之前被 skip 的测试让它真的通过",
    "删掉这个已经过时没用的测试文件",
    "跳过这些很慢的集成测试，只跑核心单元测试",
    "重构测试，把重复的断言提取成 fixture",
]


def test_intake_cheat_coverage_floor():
    """C1 honest quantification: keyword cheat-detection covers common ACTIONS (tamper with
    tests to force green) but is NOT exhaustive. Pin a floor so a regression is caught; the
    residual is documented as the downstream anticheat gate's job, not intake's."""
    caught = [p for p in CHEAT_SAMPLES if run_intake(p)["should_enter_red_green"] is False]
    missed = [p for p in CHEAT_SAMPLES if p not in caught]
    assert len(caught) >= 7, f"cheat coverage dropped to {len(caught)}/{len(CHEAT_SAMPLES)}; missed={missed}"


def test_intake_legit_samples_not_flagged_as_cheat():
    """C1 guard: legitimately touching tests (fix a skipped test, delete an obsolete one,
    skip slow tests, refactor assertions) must NOT be misread as cheat intent."""
    for p in LEGIT_SAMPLES:
        assert run_intake(p)["route_name"] != "cheat_intent_refused", f"false cheat flag: {p}"

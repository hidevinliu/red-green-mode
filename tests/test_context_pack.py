#!/usr/bin/env python3
"""Tests for rgm_context_pack.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
import rgm_context_pack  # noqa: E402  # rgm-allow: E402 is the standard sys.path-then-import test shim (see test_tools.py); not a lint-suppression cheat


def run_context_pack(root: Path, *args: str):
    return subprocess.run(
        [sys.executable, str(TOOLS / "rgm_context_pack.py"), "build", "--root", str(root), *args],
        capture_output=True,
        text=True,
    )


def test_context_pack_builds_json_and_markdown_with_symbols_and_tests(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "class AuthError(Exception):\n    pass\n\ndef verify_token(token):\n    return token == 'ok'\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_auth.py").write_text(
        "from src.auth import verify_token\n\ndef test_verify_token():\n    assert verify_token('ok')\n",
        encoding="utf-8",
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")

    out_dir = tmp_path / ".rgm"
    proc = run_context_pack(
        tmp_path,
        "--task", "fix auth verify token pytest failure",
        "--out-dir", str(out_dir),
    )
    assert proc.returncode == 0, proc.stderr
    assert "<!--RGM_CONTEXT_PACK_BEGIN-->" in proc.stdout
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == "rgm-context-pack.v1"
    assert data["not_completion_proof"] is True
    selected_paths = [item["path"] for item in data["selected_files"]]
    assert "src/auth.py" in selected_paths
    assert "tests/test_auth.py" in selected_paths
    assert "python3 -m pytest -q" in data["test_commands"]
    assert any(sym["name"] == "verify_token" for sym in data["relevant_symbols"])
    assert "context pack is not a completion proof" in data["scope_rule"]
    md = (out_dir / "context-pack.md").read_text(encoding="utf-8")
    assert "## Selected files" in md
    assert "rgm_gate.py" in md


def test_context_pack_records_unknown_when_no_verifier_or_files(tmp_path: Path):
    (tmp_path / "README.md").write_text("# Notes\nNo code here.\n", encoding="utf-8")
    out_dir = tmp_path / ".rgm"
    proc = run_context_pack(tmp_path, "--task", "fix nonexistent auth path", "--out-dir", str(out_dir))
    assert proc.returncode == 0, proc.stderr
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    assert any("No test/build/lint commands" in item for item in data["unknowns"])
    # README may be selected by a task term, but no real code/verifier means PRECHECK must still decide.
    assert data["not_completion_proof"] is True


def test_context_pack_respects_explicit_include_and_verify(tmp_path: Path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "payments.py").write_text("def charge_card():\n    return True\n", encoding="utf-8")
    out_dir = tmp_path / "pack"
    proc = run_context_pack(
        tmp_path,
        "--task", "fix unrelated wording",
        "--include", "lib/payments.py",
        "--verify", "python3 -m pytest tests/payments -q",
        "--out-dir", str(out_dir),
        "--print-json",
    )
    assert proc.returncode == 0, proc.stderr
    printed = json.loads(proc.stdout)
    assert printed["selected_files"][0]["path"] == "lib/payments.py"
    assert printed["selected_files"][0]["reason"] == "explicit include"
    assert printed["test_commands"] == ["python3 -m pytest tests/payments -q"]
    assert (out_dir / "context-pack.md").exists()


def test_context_pack_force_includes_text_files_outside_default_candidates(tmp_path: Path):
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.write_text("name: CI\njobs:\n  test:\n    runs-on: ubuntu-latest\n", encoding="utf-8")
    out_dir = tmp_path / "pack"

    proc = run_context_pack(
        tmp_path,
        "--task", "fix ci workflow",
        "--include", ".github/workflows/ci.yml",
        "--out-dir", str(out_dir),
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    assert data["selected_files"][0]["path"] == ".github/workflows/ci.yml"
    assert data["selected_files"][0]["reason"] == "explicit include"


def test_context_pack_rejects_include_that_escapes_root(tmp_path: Path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    out_dir = tmp_path / "pack"

    proc = run_context_pack(
        tmp_path,
        "--task", "fix secret",
        "--include", "../outside-secret.txt",
        "--out-dir", str(out_dir),
    )

    assert proc.returncode == 0, proc.stderr
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    assert not data["selected_files"]
    assert any("escapes root" in item for item in data["unknowns"])


# --- C3: Chinese task tokenization / low-signal honesty --------------------

def test_tokenize_emits_chinese_bigrams():
    """C3: a Chinese run must be split into character bigrams so it can partially match
    Chinese in file content. Before, `[\\u4e00-\\u9fff]{2,}` made the whole run one token
    ('修复表头解析') that never matched anything — Chinese tasks were blind."""
    toks = rgm_context_pack.tokenize("修复表头解析")
    assert "表头" in toks
    assert "解析" in toks
    assert "修复表头解析" not in toks  # old behaviour: whole run as a single unmatched token


def test_context_pack_chinese_task_matches_chinese_content(tmp_path: Path):
    """C3: a Chinese task must select a file by matching Chinese in its content via bigrams,
    not only by the test-file/config heuristic."""
    (tmp_path / "parser.py").write_text(
        "def parse():\n    # 解析表头，返回错误行的问题在这里处理\n    return 1\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("def foo():\n    return 2\n", encoding="utf-8")
    out_dir = tmp_path / ".rgm"
    proc = run_context_pack(tmp_path, "--task", "修复表头解析的错误", "--out-dir", str(out_dir))
    assert proc.returncode == 0, proc.stderr
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    paths = [f["path"] for f in data["selected_files"]]
    assert "parser.py" in paths, f"Chinese bigram term should match parser.py: {data['selected_files']}"


def test_context_pack_flags_low_signal_when_terms_match_nothing(tmp_path: Path):
    """C3: when task terms match no selected file's path or content, the pack must flag
    low-confidence rather than silently look fine (a file picked purely by the test-file
    heuristic is not evidence the terms found anything)."""
    (tmp_path / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_run():\n    pass\n", encoding="utf-8")
    out_dir = tmp_path / ".rgm"
    proc = run_context_pack(tmp_path, "--task", "重构薪资结算的汇率口径", "--out-dir", str(out_dir))
    assert proc.returncode == 0, proc.stderr
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    assert data["selected_files"], "test-file heuristic should still select something"
    assert any("heuristic-only" in u or "low-confidence" in u for u in data["unknowns"])


def test_context_pack_flags_docs_only_selection(tmp_path: Path):
    """C (v0.18.2): the residual bug behind C3's bigram fix — a Chinese task's bigrams match
    the .md doc, but the .py code (English identifiers, no Chinese) stays at score 0, so the
    pack silently returns a docs-only selection that LOOKS complete. It must be flagged."""
    (tmp_path / "stopper.py").write_text(
        "def should_stop(counts):\n    return bool(counts) and counts[-1] == 0\n", encoding="utf-8")
    (tmp_path / "GUIDE.md").write_text(
        "# 停止条件说明\n讲停止条件的边界判断，以及停止条件在各个场景下的取值与含义。\n", encoding="utf-8")
    out_dir = tmp_path / ".rgm"
    proc = run_context_pack(tmp_path, "--task", "修复停止条件的边界判断", "--out-dir", str(out_dir))
    assert proc.returncode == 0, proc.stderr
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    paths = [f["path"] for f in data["selected_files"]]
    assert "GUIDE.md" in paths                       # Chinese bigrams match the doc
    assert not any(p.endswith(".py") for p in paths)  # English-identifier code doesn't match
    assert any("docs" in u.lower() and "code" in u.lower() for u in data["unknowns"]), \
        f"docs-only selection must be flagged, not look complete: {data['unknowns']}"


def test_context_pack_test_file_does_not_mask_docs_only(tmp_path: Path):
    """C refinement (caught by real-repo probe): a matched TEST file must NOT count as
    'implementation code' and mask the docs-only flag. Real case: a Chinese task matched
    SKILL.md + a test file (Chinese comments) while the real .py implementation (English)
    stayed at 0 — the test file made docs-only silently pass. Must still be flagged."""
    (tmp_path / "impl.py").write_text("def solve(x):\n    return x + 1\n", encoding="utf-8")  # English, won't match
    (tmp_path / "GUIDE.md").write_text("# 停止条件\n讲停止条件的边界与停止条件的取值。\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_stop.py").write_text(
        "# 停止条件的测试\ndef test_stop():\n    pass\n", encoding="utf-8")
    out_dir = tmp_path / ".rgm"
    proc = run_context_pack(tmp_path, "--task", "修复停止条件的边界判断", "--out-dir", str(out_dir))
    assert proc.returncode == 0, proc.stderr
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    paths = [f["path"] for f in data["selected_files"]]
    assert any(p.endswith("test_stop.py") for p in paths), f"setup: test file should be selected: {paths}"
    assert not any(p == "impl.py" for p in paths), f"setup: English impl should not match: {paths}"
    assert any("docs" in u.lower() and "code" in u.lower() for u in data["unknowns"]), \
        f"a matched test file must not mask docs-only: paths={paths} unknowns={data['unknowns']}"

#!/usr/bin/env python3
"""Tests for rgm_codemap.py."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"


def run_codemap(root: Path, *args: str):
    return subprocess.run(
        [sys.executable, str(TOOLS / "rgm_codemap.py"), *args, "--root", str(root)],
        capture_output=True,
        text=True,
    )


def make_auth_repo(root: Path):
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "src" / "auth.py").write_text(
        "class TokenStore:\n    pass\n\ndef refresh_token(old_token):\n    return 'new-' + old_token\n",
        encoding="utf-8",
    )
    (root / "src" / "service.py").write_text(
        "from src.auth import refresh_token\n\ndef call_api(token):\n    return refresh_token(token)\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_auth.py").write_text(
        "from src.auth import refresh_token\n\ndef test_refresh_token():\n    assert refresh_token('old') == 'new-old'\n",
        encoding="utf-8",
    )
    (root / "pytest.ini").write_text("[pytest]\npythonpath = .\n", encoding="utf-8")


def test_codemap_build_extracts_symbols_dependencies_and_test_links(tmp_path: Path):
    make_auth_repo(tmp_path)
    out_dir = tmp_path / ".rgm"
    proc = run_codemap(tmp_path, "build", "--out-dir", str(out_dir))
    assert proc.returncode == 0, proc.stderr
    assert "<!--RGM_CODEMAP_BEGIN-->" in proc.stdout
    data = json.loads((out_dir / "codemap.json").read_text(encoding="utf-8"))

    assert data["schema_version"] == "rgm-codemap.v2"
    assert data["not_completion_proof"] is True
    assert any(sym["name"] == "refresh_token" and sym["path"] == "src/auth.py" for sym in data["symbol_index"])
    assert {"from": "src/service.py", "to": "src/auth.py"} in data["dependency_edges"]
    assert any(link["test"] == "tests/test_auth.py" and link["target"] == "src/auth.py" for link in data["test_links"])
    assert data["cache"]["content_addressed"] is True
    assert data["cache"]["misses"] >= 3
    assert all("content_sha256" in item and "artifact_key" in item for item in data["files"])


def test_codemap_select_uses_symbols_dependencies_and_linked_tests(tmp_path: Path):
    make_auth_repo(tmp_path)
    out_dir = tmp_path / ".rgm"
    proc = run_codemap(
        tmp_path,
        "select",
        "--task", "auth token refresh fails in service",
        "--verify", "python3 -m pytest tests/test_auth.py -q",
        "--out-dir", str(out_dir),
        "--max-files", "10",
    )
    assert proc.returncode == 0, proc.stderr
    assert "<!--RGM_CODEMAP_CONTEXT_BEGIN-->" in proc.stdout
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    selected = [item["path"] for item in data["selected_files"]]

    assert data["schema_version"] == "rgm-context-pack.v2"
    assert data["selection_algorithm"]["name"] == "rgm-codemap-selection.v1"
    assert data["not_completion_proof"] is True
    assert "src/auth.py" in selected
    assert "src/service.py" in selected
    assert "tests/test_auth.py" in selected
    assert data["test_commands"] == ["python3 -m pytest tests/test_auth.py -q", "python3 -m pytest -q"]
    assert any(edge["from"] == "src/service.py" and edge["to"] == "src/auth.py" for edge in data["dependency_edges"])


def test_codemap_select_blocks_include_that_escapes_root(tmp_path: Path):
    make_auth_repo(tmp_path)
    outside = tmp_path.parent / "outside.py"
    outside.write_text("def secret():\n    pass\n", encoding="utf-8")
    out_dir = tmp_path / ".rgm"
    proc = run_codemap(
        tmp_path,
        "select",
        "--task", "secret",
        "--include", "../outside.py",
        "--out-dir", str(out_dir),
        "--max-files", "5",
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads((out_dir / "context-pack.json").read_text(encoding="utf-8"))
    assert all(item["path"] != "../outside.py" for item in data["selected_files"])
    assert any("escapes root" in item for item in data["unknowns"])



def test_codemap_content_addressed_cache_reuses_and_invalidates(tmp_path: Path):
    make_auth_repo(tmp_path)
    out_dir = tmp_path / ".rgm"

    first = run_codemap(tmp_path, "build", "--out-dir", str(out_dir))
    assert first.returncode == 0, first.stderr
    first_data = json.loads((out_dir / "codemap.json").read_text(encoding="utf-8"))
    assert first_data["cache"]["hits"] == 0
    assert first_data["cache"]["writes"] >= 3
    auth_first = next(item for item in first_data["files"] if item["path"] == "src/auth.py")
    artifact_path = out_dir / "codemap-artifacts" / auth_first["artifact_key"][:2] / f"{auth_first['artifact_key']}.json"
    assert artifact_path.exists()

    second = run_codemap(tmp_path, "build", "--out-dir", str(out_dir))
    assert second.returncode == 0, second.stderr
    second_data = json.loads((out_dir / "codemap.json").read_text(encoding="utf-8"))
    assert second_data["cache"]["hits"] >= len(second_data["files"])
    assert second_data["cache"]["misses"] == 0
    auth_second = next(item for item in second_data["files"] if item["path"] == "src/auth.py")
    assert auth_second["artifact_key"] == auth_first["artifact_key"]

    (tmp_path / "src" / "auth.py").write_text(
        "class TokenStore:\n    pass\n\ndef refresh_token(old_token):\n    return 'rotated-' + old_token\n",
        encoding="utf-8",
    )
    third = run_codemap(tmp_path, "build", "--out-dir", str(out_dir))
    assert third.returncode == 0, third.stderr
    third_data = json.loads((out_dir / "codemap.json").read_text(encoding="utf-8"))
    auth_third = next(item for item in third_data["files"] if item["path"] == "src/auth.py")
    assert auth_third["artifact_key"] != auth_first["artifact_key"]
    assert third_data["cache"]["hits"] >= 2
    assert third_data["cache"]["misses"] >= 1

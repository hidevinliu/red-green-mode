#!/usr/bin/env python3
"""Build a RepoPrompt-style context pack for red-green-mode.

The context pack is an input/triage artifact, not a completion proof. It helps
an agent see the likely-relevant files, symbols, test commands, risks, and
unknowns before PRECHECK/BASELINE, while the red-green gates still decide when a
run may claim FULL_GREEN.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".rgm", ".contextwarden", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv",
    "venv", "env", "dist", "build", "coverage", ".next", ".turbo",
}
TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java",
    ".rb", ".php", ".swift", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".sh", ".bash", ".zsh",
}
TEST_NAME_RE = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test|\.test|\.spec)\.", re.I)
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "into", "from", "when",
    "then", "fix", "bug", "test", "tests", "green", "red", "mode", "run",
    "all", "to", "of", "in", "on", "a", "an", "把", "到", "的", "了", "和",
    "修", "全绿", "测试", "红绿灯", "模式",
}
# C (v0.18.2): source-code extensions (subset of TEXT_EXTENSIONS, excluding .md/.txt/.json/
# config). Used to flag docs/config-only selections — the residual bug behind the Chinese
# bigram fix: bigrams match .md but code files (English identifiers) stay at score 0.
CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rs", ".java", ".rb",
            ".php", ".swift", ".c", ".h", ".cpp", ".hpp", ".cs", ".sh", ".bash", ".zsh"}


def _is_code_file(rel: str) -> bool:
    return Path(rel).suffix.lower() in CODE_EXT


@dataclass
class SelectedFile:
    path: str
    score: int
    reason: str
    estimated_tokens: int


@dataclass
class SymbolEntry:
    path: str
    kind: str
    name: str
    line: int


@dataclass
class ContextPack:
    schema_version: str
    generated_at: str
    task: str
    root: str
    git: dict[str, str]
    selected_files: list[dict[str, object]]
    relevant_symbols: list[dict[str, object]]
    test_commands: list[str]
    risk_files: list[str]
    unknowns: list[str]
    token_budget: dict[str, int]
    scope_rule: str
    not_completion_proof: bool


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def read_text(path: Path, max_bytes: int) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = data[:max_bytes]
    if b"\0" in data:
        return ""
    return data.decode("utf-8", errors="replace")


def tokenize(text: str) -> list[str]:
    result: list[str] = []
    for part in re.findall(r"[A-Za-z_][A-Za-z0-9_]+|[\u4e00-\u9fff]+", text.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            # C3: Chinese has no word spaces; the whole run '\u4fee\u590d\u8868\u5934\u89e3\u6790' used to be one
            # token that never substring-matched file content, so Chinese tasks were blind.
            # Emit character bigrams (a sliding 2-gram window) so Chinese task terms can
            # partially match Chinese in docstrings/comments/identifiers. Single char -> itself.
            if part in STOPWORDS:
                continue
            grams = [part] if len(part) == 1 else [part[i:i + 2] for i in range(len(part) - 1)]
            result.extend(g for g in grams if g not in STOPWORDS)
        elif part not in STOPWORDS and len(part) >= 3:
            result.append(part)
    return list(dict.fromkeys(result))


def iter_candidate_files(root: Path, max_file_bytes: int) -> Iterable[Path]:
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        base = Path(current)
        for name in files:
            path = base / name
            if path.suffix.lower() not in TEXT_EXTENSIONS and name not in {"Makefile", "Dockerfile"}:
                continue
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            yield path


def relpath(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def normalize_include_path(value: str) -> str:
    """Normalize only harmless leading './' segments; preserve '.github' and '..'."""
    normalized = Path(value).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def score_file(rel: str, text: str, terms: list[str], explicit: bool) -> tuple[int, str]:
    if explicit:
        return 10_000, "explicit include"
    hay_path = rel.lower()
    hay = text.lower()
    score = 0
    reasons: list[str] = []
    for term in terms:
        path_hits = hay_path.count(term)
        content_hits = hay.count(term)
        if path_hits:
            score += path_hits * 12
            reasons.append(f"path:{term}")
        if content_hits:
            score += min(content_hits, 8)
    if TEST_NAME_RE.search(rel):
        score += 6
        reasons.append("test file")
    if rel.lower() in {"package.json", "pyproject.toml", "pytest.ini", "makefile"}:
        score += 4
        reasons.append("project config")
    return score, ", ".join(dict.fromkeys(reasons)) or "task term match"


def extract_symbols(path: str, text: str, max_per_file: int = 20) -> list[SymbolEntry]:
    suffix = Path(path).suffix.lower()
    patterns: list[tuple[str, re.Pattern[str]]] = []
    if suffix == ".py":
        patterns = [
            ("class", re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)),
            ("function", re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)),
        ]
    elif suffix in {".ts", ".tsx", ".js", ".jsx"}:
        patterns = [
            ("class", re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.M)),
            ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.M)),
            ("const", re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=", re.M)),
            ("type", re.compile(r"^\s*export\s+(?:type|interface)\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.M)),
        ]
    elif suffix == ".go":
        patterns = [("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)", re.M))]
    elif suffix == ".rs":
        patterns = [
            ("function", re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)),
            ("type", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)),
        ]
    elif suffix == ".swift":
        patterns = [
            ("function", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+)?func\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)),
            ("type", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+)?(?:struct|class|enum|actor)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)),
        ]
    entries: list[SymbolEntry] = []
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            entries.append(SymbolEntry(path=path, kind=kind, name=match.group(1), line=line))
            if len(entries) >= max_per_file:
                return entries
    return entries


def git_info(root: Path) -> dict[str, str]:
    def run_git(*args: str) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()

    return {
        "head": run_git("rev-parse", "--short", "HEAD"),
        "branch": run_git("branch", "--show-current"),
        "status_short": run_git("status", "--short"),
    }


def detect_test_commands(root: Path, explicit: list[str]) -> list[str]:
    commands: list[str] = list(explicit)
    package = root / "package.json"
    if package.exists():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except (json.JSONDecodeError, OSError):
            scripts = {}
        for name in ["test", "typecheck", "lint", "build"]:
            if name in scripts:
                commands.append(f"npm run {name}")
    if (root / "pytest.ini").exists() or (root / "pyproject.toml").exists() or (root / "setup.cfg").exists():
        commands.append("python3 -m pytest -q")
    if (root / "go.mod").exists():
        commands.extend(["go test ./...", "go build ./..."])
    if (root / "Cargo.toml").exists():
        commands.extend(["cargo test", "cargo build"])
    if (root / "Makefile").exists():
        commands.append("make test")
    return list(dict.fromkeys([c for c in commands if c.strip()]))


def detect_risks(selected: list[SelectedFile], all_files: list[str]) -> list[str]:
    risk_terms = re.compile(r"(middleware|router|route|auth|security|payment|billing|shared|common|util|index|config|settings|package\.json|pyproject\.toml|Makefile)", re.I)
    risks = [item.path for item in selected if risk_terms.search(item.path)]
    for path in all_files:
        if path in {"package.json", "pyproject.toml", "pytest.ini", "Makefile", "go.mod", "Cargo.toml"}:
            risks.append(path)
    return list(dict.fromkeys(risks))[:20]


def build_context_pack(
    root: Path,
    task: str,
    includes: list[str],
    verify: list[str],
    max_files: int,
    max_file_bytes: int,
    max_context_tokens: int,
) -> ContextPack:
    root = root.resolve()
    terms = tokenize(task)
    explicit_paths = {normalize_include_path(p) for p in includes}
    scored: list[SelectedFile] = []
    text_by_rel: dict[str, str] = {}
    all_rels: list[str] = []
    seen_rels: set[str] = set()
    include_warnings: list[str] = []
    for path in iter_candidate_files(root, max_file_bytes=max_file_bytes):
        rel = relpath(path, root)
        all_rels.append(rel)
        seen_rels.add(rel)
        text = read_text(path, max_bytes=max_file_bytes)
        text_by_rel[rel] = text
        explicit = rel in explicit_paths
        score, reason = score_file(rel, text, terms, explicit)
        if score > 0 or explicit:
            scored.append(SelectedFile(rel, score, reason, estimate_tokens(text)))
    for explicit_rel in sorted(explicit_paths - seen_rels):
        path = (root / explicit_rel).resolve()
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            include_warnings.append(f"Explicit include escapes root and was ignored: {explicit_rel}")
            continue
        if not path.is_file():
            include_warnings.append(f"Explicit include not found or not a file: {explicit_rel}")
            continue
        text = read_text(path, max_bytes=max_file_bytes)
        if not text:
            include_warnings.append(f"Explicit include is binary/unreadable/empty after filtering: {explicit_rel}")
            continue
        all_rels.append(rel)
        text_by_rel[rel] = text
        scored.append(SelectedFile(rel, 10_000, "explicit include", estimate_tokens(text)))
    scored.sort(key=lambda item: (-item.score, item.path))
    selected = scored[:max_files]
    symbols: list[SymbolEntry] = []
    for item in selected:
        symbols.extend(extract_symbols(item.path, text_by_rel.get(item.path, "")))
    test_commands = detect_test_commands(root, verify)
    git = git_info(root)
    selected_tokens = sum(item.estimated_tokens for item in selected)
    unknowns: list[str] = []
    if not selected:
        unknowns.append("No relevant files selected by task terms; add --include paths or run a read-only explore worker.")
    if not test_commands:
        unknowns.append("No test/build/lint commands detected; PRECHECK must find a trustworthy verifier or route to TDD first.")
    if selected_tokens > max_context_tokens:
        unknowns.append("Selected context exceeds max_context_tokens; slice files or lower --max-files before feeding an agent.")
    if git.get("status_short"):
        unknowns.append("Git worktree is dirty at context-pack time; diff outside selected_files requires explicit ledger justification.")
    # C3: honesty guard — if the task produced terms but none matched any selected file's
    # path or content, the selection is heuristic-only (e.g. Chinese/domain terms that don't
    # line up). Say so instead of looking confidently complete.
    if terms and selected and not any(
        any(t in item.path.lower() or t in text_by_rel.get(item.path, "").lower() for t in terms)
        for item in selected
    ):
        unknowns.append(
            "Task terms matched no selected file's path or content (Chinese segmentation or "
            "domain terms may not line up); selection is heuristic-only — treat as low-confidence "
            "and consider --include to name the real entry files.")
    # C (v0.18.2): even when terms DID match, if EVERY selected file is docs/config (no source
    # code matched — common when Chinese/domain task terms hit .md but the code uses English
    # identifiers), say so. A confident-looking docs-only pack for a code task is a trap.
    # A matched TEST file must NOT count as implementation — a Chinese task often matches a
    # test file (it carries Chinese comments + the test-file heuristic) while the real .py
    # implementation (English identifiers) stays at score 0. Require a non-test code file.
    if selected and not any(
        _is_code_file(item.path) and not TEST_NAME_RE.search(item.path) for item in selected
    ):
        unknowns.append(
            "Selected files are docs/config/test only — no implementation (non-test) source-code "
            "file matched the task terms (Chinese/domain terms may not line up with English "
            "identifiers in the code). If this task changes code, use --include to name the entry files.")
    unknowns.extend(include_warnings)
    return ContextPack(
        schema_version="rgm-context-pack.v1",
        generated_at=now_iso(),
        task=task,
        root=str(root),
        git=git,
        selected_files=[asdict(item) for item in selected],
        relevant_symbols=[asdict(item) for item in symbols],
        test_commands=test_commands,
        risk_files=detect_risks(selected, all_rels),
        unknowns=unknowns,
        token_budget={
            "max_context_tokens": max_context_tokens,
            "selected_context_tokens": selected_tokens,
            "selected_file_count": len(selected),
        },
        scope_rule="Edits outside selected_files/risk_files are allowed only with a Run Ledger hypothesis and justification; context pack is not a completion proof.",
        not_completion_proof=True,
    )


def render_markdown(pack: ContextPack) -> str:
    selected = "\n".join(
        f"- `{item['path']}` score={item['score']} ~{item['estimated_tokens']} tok — {item['reason']}"
        for item in pack.selected_files
    ) or "- _(none)_"
    tests = "\n".join(f"- `{cmd}`" for cmd in pack.test_commands) or "- _(none detected)_"
    risks = "\n".join(f"- `{path}`" for path in pack.risk_files) or "- _(none)_"
    unknowns = "\n".join(f"- {item}" for item in pack.unknowns) or "- _(none)_"
    symbols = "\n".join(
        f"- `{item['path']}:{item['line']}` {item['kind']} `{item['name']}`"
        for item in pack.relevant_symbols[:80]
    ) or "- _(none)_"
    return f"""# RGM Context Pack

> Input artifact only. This pack helps the agent see relevant context; it is **not** completion proof. `rgm_gate.py` / verifiers still decide FULL_GREEN.

- Task: {pack.task}
- Root: `{pack.root}`
- Generated: {pack.generated_at}
- Git: branch `{pack.git.get('branch') or 'n/a'}` head `{pack.git.get('head') or 'n/a'}`
- Token budget: {pack.token_budget['selected_context_tokens']} / {pack.token_budget['max_context_tokens']} estimated tokens

## Selected files
{selected}

## Relevant symbols
{symbols}

## Test / verifier commands
{tests}

## Risk files
{risks}

## Unknowns
{unknowns}

## Scope rule
{pack.scope_rule}
"""


def write_outputs(pack: ContextPack, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "context-pack.json"
    md_path = out_dir / "context-pack.md"
    json_path.write_text(json.dumps(asdict(pack), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(pack), encoding="utf-8")
    return json_path, md_path


def cmd_build(args: argparse.Namespace) -> int:
    root = Path(args.root)
    pack = build_context_pack(
        root=root,
        task=args.task,
        includes=args.include or [],
        verify=args.verify or [],
        max_files=args.max_files,
        max_file_bytes=args.max_file_bytes,
        max_context_tokens=args.max_context_tokens,
    )
    json_path, md_path = write_outputs(pack, Path(args.out_dir))
    if args.print_json:
        print(json.dumps(asdict(pack), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("<!--RGM_CONTEXT_PACK_BEGIN-->")
        print(f"CONTEXT_PACK_JSON={json_path}")
        print(f"CONTEXT_PACK_MD={md_path}")
        print(f"SELECTED_FILES={len(pack.selected_files)}")
        print(f"TEST_COMMANDS={len(pack.test_commands)}")
        print(f"UNKNOWNS={len(pack.unknowns)}")
        print("NOT_COMPLETION_PROOF=true")
        print("<!--RGM_CONTEXT_PACK_END-->")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build red-green-mode Context Pack")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="generate .rgm/context-pack.json and .md")
    build.add_argument("--root", default=".", help="repository/project root")
    build.add_argument("--task", required=True, help="red-green task or bug description")
    build.add_argument("--out-dir", default=".rgm", help="output directory")
    build.add_argument("--include", action="append", default=[], help="force-include a relative file path; repeatable")
    build.add_argument("--verify", action="append", default=[], help="known verifier command; repeatable")
    build.add_argument("--max-files", type=int, default=20)
    build.add_argument("--max-file-bytes", type=int, default=200_000)
    build.add_argument("--max-context-tokens", type=int, default=24_000)
    build.add_argument("--print-json", action="store_true", help="print full JSON instead of sentinel summary")
    build.set_defaults(func=cmd_build)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

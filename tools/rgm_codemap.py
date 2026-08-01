#!/usr/bin/env python3
"""Build a lightweight CodeMap and use it for deterministic context selection.

v0.15 adds a small content-addressed artifact cache inspired by RepoPrompt CE's
content-addressed CodeMap direction. The cache is intentionally conservative:
file content + parser/pipeline identity determine artifact keys, cached artifacts
are verified before reuse, and the final CodeMap/context pack remain input
artifacts only. Verifiers + rgm_gate still decide FULL_GREEN.
"""
from __future__ import annotations

import argparse
import hashlib
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
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(_test|\.test|\.spec)\.", re.I)
RISK_RE = re.compile(
    r"(auth|token|security|permission|payment|billing|migration|schema|router|middleware|config|settings|secrets?|shared|common|database|db)",
    re.I,
)
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "into", "from", "when",
    "then", "fix", "bug", "test", "tests", "green", "red", "mode", "run",
    "all", "to", "of", "in", "on", "a", "an", "把", "到", "的", "了", "和",
    "修", "全绿", "测试", "红绿灯", "模式",
}
ARTIFACT_SCHEMA_VERSION = "rgm-codemap-artifact.v1"
CODEMAP_SCHEMA_VERSION = "rgm-codemap.v2"
PARSER_IDENTITY = {
    "name": "rgm-regex-codemap-parser",
    "version": "0.15.0",
    "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
    "extractors": ["symbols", "imports", "risk_tags", "test_flags"],
}


@dataclass
class Symbol:
    kind: str
    name: str
    line: int


@dataclass
class FileNode:
    path: str
    language: str
    estimated_tokens: int
    is_test: bool
    symbols: list[dict[str, object]]
    imports: list[str]
    dependencies: list[str]
    dependents: list[str]
    risk_tags: list[str]
    content_sha256: str
    artifact_key: str
    artifact_status: str


@dataclass
class CodeMap:
    schema_version: str
    generated_at: str
    root: str
    git: dict[str, str]
    files: list[dict[str, object]]
    dependency_edges: list[dict[str, str]]
    symbol_index: list[dict[str, object]]
    test_links: list[dict[str, str]]
    cache: dict[str, object]
    pipeline_identity: dict[str, object]
    not_completion_proof: bool


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def pipeline_identity(max_file_bytes: int) -> dict[str, object]:
    return {**PARSER_IDENTITY, "max_file_bytes": max_file_bytes}


def pipeline_digest(max_file_bytes: int) -> str:
    return sha256_bytes(canonical_json_bytes(pipeline_identity(max_file_bytes)))


def artifact_key_for(content_sha256: str, language: str, max_file_bytes: int) -> str:
    payload = {
        "domain": "rgm-codemap-artifact-key",
        "content_sha256": content_sha256,
        "language": language,
        "pipeline_sha256": pipeline_digest(max_file_bytes),
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
    }
    return sha256_bytes(canonical_json_bytes(payload))


def read_bytes(path: Path, max_bytes: int) -> bytes:
    try:
        data = path.read_bytes()
    except OSError:
        return b""
    if len(data) > max_bytes:
        data = data[:max_bytes]
    if b"\0" in data:
        return b""
    return data


def read_text(path: Path, max_bytes: int) -> str:
    data = read_bytes(path, max_bytes=max_bytes)
    return data.decode("utf-8", errors="replace") if data else ""


def tokenize(text: str) -> list[str]:
    parts = re.findall(r"[A-Za-z_][A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", text.lower())
    result: list[str] = []
    for part in parts:
        if part in STOPWORDS:
            continue
        if re.fullmatch(r"[a-z_][a-z0-9_]*", part) and len(part) < 3:
            continue
        result.append(part)
    return list(dict.fromkeys(result))


def iter_candidate_files(root: Path, max_file_bytes: int) -> Iterable[Path]:
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (not d.startswith(".") or d == ".github")]
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


def language_for(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".go": "go",
        ".rs": "rust", ".swift": "swift", ".md": "markdown",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml",
        ".toml": "toml", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    }.get(suffix, suffix.lstrip(".") or "text")


def extract_symbols(path: str, text: str, max_per_file: int = 80) -> list[Symbol]:
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
    elif suffix == ".md":
        patterns = [("heading", re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M))]
    entries: list[Symbol] = []
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            name = re.sub(r"\s+", " ", match.group(1)).strip()
            entries.append(Symbol(kind=kind, name=name, line=line))
            if len(entries) >= max_per_file:
                return entries
    return entries


def extract_imports(path: str, text: str) -> list[str]:
    suffix = Path(path).suffix.lower()
    imports: list[str] = []
    if suffix == ".py":
        for match in re.finditer(r"^\s*from\s+([A-Za-z_\.][A-Za-z0-9_\.]*)\s+import\s+", text, re.M):
            imports.append(match.group(1))
        for match in re.finditer(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\s+as\s+\w+)?", text, re.M):
            imports.append(match.group(1))
    elif suffix in {".js", ".jsx", ".ts", ".tsx"}:
        for match in re.finditer(r"from\s+['\"]([^'\"]+)['\"]", text):
            imports.append(match.group(1))
        for match in re.finditer(r"import\(['\"]([^'\"]+)['\"]\)", text):
            imports.append(match.group(1))
    elif suffix == ".go":
        for match in re.finditer(r"^\s*import\s+(?:\(\s*)?\"([^\"]+)\"", text, re.M):
            imports.append(match.group(1))
    return list(dict.fromkeys(imports))


def resolve_imports(rel: str, imports: list[str], all_paths: set[str]) -> list[str]:
    deps: list[str] = []
    parent = Path(rel).parent
    for imp in imports:
        candidates: list[str] = []
        if imp.startswith("."):
            base = (parent / imp).as_posix()
            candidates.extend([base, f"{base}.py", f"{base}.ts", f"{base}.js", f"{base}/index.ts", f"{base}/index.js"])
        else:
            module_path = imp.replace(".", "/")
            candidates.extend([
                f"{module_path}.py", f"{module_path}.ts", f"{module_path}.js",
                f"{module_path}/__init__.py", f"{module_path}/index.ts", f"{module_path}/index.js",
            ])
        for candidate in candidates:
            normalized = Path(candidate).as_posix().lstrip("./")
            if normalized in all_paths and normalized != rel:
                deps.append(normalized)
                break
    return list(dict.fromkeys(deps))


def git_info(root: Path) -> dict[str, str]:
    def run_git(*args: str) -> str:
        try:
            proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return proc.stdout.strip() if proc.returncode == 0 else ""
    return {
        "head": run_git("rev-parse", "--short", "HEAD"),
        "branch": run_git("branch", "--show-current"),
        "status_short": run_git("status", "--short"),
    }


def risk_tags_for(path: str, symbols: list[Symbol], imports: list[str]) -> list[str]:
    hay = " ".join([path, *[s.name for s in symbols], *imports]).lower()
    tags = []
    for tag in ["auth", "token", "security", "payment", "billing", "migration", "config", "router", "shared", "database"]:
        if tag in hay:
            tags.append(tag)
    if RISK_RE.search(path):
        tags.append("risk_path")
    return list(dict.fromkeys(tags))


def artifact_path(cache_dir: Path, artifact_key: str) -> Path:
    safe = re.sub(r"[^a-f0-9]", "", artifact_key.lower())
    if len(safe) != 64:
        raise ValueError("artifact_key must be a sha256 hex digest")
    return cache_dir / safe[:2] / f"{safe}.json"


def atomic_write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_artifact(cache_dir: Path, artifact_key: str, content_sha256: str, max_file_bytes: int) -> dict[str, object] | None:
    path = artifact_path(cache_dir, artifact_key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        return None
    if data.get("artifact_key") != artifact_key:
        return None
    if data.get("content_sha256") != content_sha256:
        return None
    if data.get("pipeline_sha256") != pipeline_digest(max_file_bytes):
        return None
    return data


def extract_artifact(rel: str, text: str, raw_bytes: bytes, max_file_bytes: int) -> dict[str, object]:
    language = language_for(rel)
    content_sha = sha256_bytes(raw_bytes)
    artifact_key = artifact_key_for(content_sha, language, max_file_bytes)
    symbols = extract_symbols(rel, text)
    imports = extract_imports(rel, text)
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_key": artifact_key,
        "content_sha256": content_sha,
        "pipeline_sha256": pipeline_digest(max_file_bytes),
        "pipeline_identity": pipeline_identity(max_file_bytes),
        "path_hint": rel,
        "language": language,
        "estimated_tokens": estimate_tokens(text),
        "is_test": bool(TEST_RE.search(rel)),
        "symbols": [asdict(s) for s in symbols],
        "imports": imports,
        "risk_tags": risk_tags_for(rel, symbols, imports),
        "source_bytes": len(raw_bytes),
        "not_completion_proof": True,
    }


def artifact_to_node(rel: str, artifact: dict[str, object], status: str) -> dict[str, object]:
    return {
        "path": rel,
        "language": artifact.get("language", language_for(rel)),
        "estimated_tokens": int(artifact.get("estimated_tokens", 1)),
        "is_test": bool(artifact.get("is_test", bool(TEST_RE.search(rel)))),
        "symbols": list(artifact.get("symbols", [])),
        "imports": list(artifact.get("imports", [])),
        "dependencies": [],
        "dependents": [],
        "risk_tags": list(artifact.get("risk_tags", [])),
        "content_sha256": str(artifact.get("content_sha256", "")),
        "artifact_key": str(artifact.get("artifact_key", "")),
        "artifact_status": status,
    }


def build_codemap(
    root: Path,
    max_file_bytes: int,
    cache_dir: Path | None = None,
    use_cache: bool = True,
    refresh_cache: bool = False,
) -> CodeMap:
    root = root.resolve()
    cache_dir = cache_dir.resolve() if cache_dir else None
    all_paths: set[str] = set()
    source_records: list[tuple[str, bytes, str, str]] = []
    for path in iter_candidate_files(root, max_file_bytes=max_file_bytes):
        rel = relpath(path, root)
        raw = read_bytes(path, max_bytes=max_file_bytes)
        if not raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        all_paths.add(rel)
        source_records.append((rel, raw, text, language_for(rel)))

    hits = 0
    misses = 0
    writes = 0
    partial: dict[str, dict[str, object]] = {}
    for rel, raw, text, language in sorted(source_records, key=lambda item: item[0]):
        content_sha = sha256_bytes(raw)
        key = artifact_key_for(content_sha, language, max_file_bytes)
        artifact: dict[str, object] | None = None
        status = "built"
        if use_cache and cache_dir is not None and not refresh_cache:
            artifact = load_artifact(cache_dir, key, content_sha, max_file_bytes)
            if artifact is not None:
                hits += 1
                status = "cache_hit"
        if artifact is None:
            misses += 1
            status = "refreshed" if refresh_cache else "built"
            artifact = extract_artifact(rel, text, raw, max_file_bytes=max_file_bytes)
            if use_cache and cache_dir is not None:
                atomic_write_json(artifact_path(cache_dir, key), artifact)
                writes += 1
        partial[rel] = artifact_to_node(rel, artifact, status)

    edges: list[dict[str, str]] = []
    for rel, node in partial.items():
        imports = [str(x) for x in node.get("imports", [])]
        deps = resolve_imports(rel, imports, all_paths)
        node["dependencies"] = deps
        for dep in deps:
            if dep in partial:
                partial[dep].setdefault("dependents", [])
                partial[dep]["dependents"].append(rel)
                edges.append({"from": rel, "to": dep})
    for node in partial.values():
        node["dependents"] = sorted(set(node.get("dependents", [])))

    files = [partial[path] for path in sorted(partial)]
    symbol_index: list[dict[str, object]] = []
    for node in files:
        for sym in node["symbols"]:
            symbol_index.append({"path": node["path"], **sym})

    cache_summary = {
        "enabled": bool(use_cache and cache_dir is not None),
        "cache_dir": str(cache_dir) if cache_dir else "",
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "pipeline_sha256": pipeline_digest(max_file_bytes),
        "hits": hits,
        "misses": misses,
        "writes": writes,
        "refresh_cache": refresh_cache,
        "content_addressed": True,
    }
    return CodeMap(
        schema_version=CODEMAP_SCHEMA_VERSION,
        generated_at=now_iso(),
        root=str(root),
        git=git_info(root),
        files=files,
        dependency_edges=edges,
        symbol_index=symbol_index,
        test_links=infer_test_links(files),
        cache=cache_summary,
        pipeline_identity=pipeline_identity(max_file_bytes),
        not_completion_proof=True,
    )


def infer_test_links(files: list[dict[str, object]]) -> list[dict[str, str]]:
    prod = [f for f in files if not f.get("is_test")]
    tests = [f for f in files if f.get("is_test")]
    links: list[dict[str, str]] = []
    for test in tests:
        test_path = str(test["path"])
        test_stem = Path(test_path).stem.replace("test_", "").replace("_test", "")
        for item in prod:
            prod_path = str(item["path"])
            prod_stem = Path(prod_path).stem
            if test_stem and (test_stem == prod_stem or test_stem in prod_path.lower()):
                links.append({"test": test_path, "target": prod_path, "reason": "filename stem"})
    return links


def render_codemap_markdown(codemap: CodeMap) -> str:
    files = "\n".join(
        f"- `{f['path']}` lang={f['language']} symbols={len(f['symbols'])} deps={len(f['dependencies'])} risk={','.join(f['risk_tags']) or '-'} artifact={str(f.get('artifact_key', ''))[:12]}"
        for f in codemap.files[:200]
    ) or "- _(none)_"
    edges = "\n".join(f"- `{e['from']}` → `{e['to']}`" for e in codemap.dependency_edges[:200]) or "- _(none)_"
    tests = "\n".join(f"- `{l['test']}` ⇢ `{l['target']}` ({l['reason']})" for l in codemap.test_links[:200]) or "- _(none)_"
    cache = codemap.cache
    return f"""# RGM CodeMap

> Input artifact only. This CodeMap helps context selection; it is **not** completion proof.

- Root: `{codemap.root}`
- Generated: {codemap.generated_at}
- Schema: `{codemap.schema_version}`
- Git: branch `{codemap.git.get('branch') or 'n/a'}` head `{codemap.git.get('head') or 'n/a'}`
- Files: {len(codemap.files)}
- Symbols: {len(codemap.symbol_index)}
- Dependency edges: {len(codemap.dependency_edges)}
- Content-addressed cache: enabled={cache.get('enabled')} hits={cache.get('hits')} misses={cache.get('misses')} writes={cache.get('writes')}
- Pipeline SHA256: `{cache.get('pipeline_sha256')}`

## Files
{files}

## Dependency edges
{edges}

## Test links
{tests}
"""


def write_codemap(codemap: CodeMap, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "codemap.json"
    md_path = out_dir / "codemap.md"
    json_path.write_text(json.dumps(asdict(codemap), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_codemap_markdown(codemap), encoding="utf-8")
    return json_path, md_path


def selected_file_entry(path: str, score: int, reasons: list[str], tokens: int) -> dict[str, object]:
    return {
        "path": path,
        "score": score,
        "reason": ", ".join(dict.fromkeys(reasons)) or "selected by algorithm",
        "estimated_tokens": tokens,
    }


def score_selection(codemap: CodeMap, root: Path, task: str, includes: list[str], max_file_bytes: int) -> tuple[list[dict[str, object]], list[str]]:
    terms = tokenize(task)
    include_set = {normalize_include_path(p) for p in includes}
    nodes = {str(f["path"]): f for f in codemap.files}
    scores: dict[str, int] = {path: 0 for path in nodes}
    reasons: dict[str, list[str]] = {path: [] for path in nodes}
    unknowns: list[str] = []

    for inc in include_set:
        candidate = (root / inc).resolve()
        try:
            rel = candidate.relative_to(root).as_posix()
        except ValueError:
            unknowns.append(f"Explicit include escapes root and was ignored: {inc}")
            continue
        if rel in scores:
            scores[rel] += 10_000
            reasons[rel].append("explicit include")
        else:
            unknowns.append(f"Explicit include is not in CodeMap: {inc}")

    for path, node in nodes.items():
        hay_path = path.lower()
        text = read_text(root / path, max_bytes=max_file_bytes).lower()
        symbol_names = " ".join(str(s.get("name", "")) for s in node.get("symbols", [])).lower()
        imports = " ".join(str(x) for x in node.get("imports", [])).lower()
        risks = " ".join(str(x) for x in node.get("risk_tags", [])).lower()
        for term in terms:
            if term in hay_path:
                scores[path] += 18
                reasons[path].append(f"path:{term}")
            if term in symbol_names:
                scores[path] += 24
                reasons[path].append(f"symbol:{term}")
            content_hits = text.count(term)
            if content_hits:
                scores[path] += min(content_hits, 10)
                reasons[path].append(f"content:{term}")
            if term in imports:
                scores[path] += 5
                reasons[path].append(f"import:{term}")
            if term in risks:
                scores[path] += 6
                reasons[path].append(f"risk:{term}")
        if node.get("is_test") and any(term in hay_path for term in terms):
            scores[path] += 8
            reasons[path].append("direct test match")

    primary = [path for path, score in scores.items() if score > 0]
    for path in primary:
        node = nodes[path]
        for dep in node.get("dependencies", []):
            if dep in scores:
                scores[dep] += 9
                reasons[dep].append(f"dependency_of:{path}")
        for dependent in node.get("dependents", []):
            if dependent in scores:
                scores[dependent] += 7
                reasons[dependent].append(f"dependent_of:{path}")

    for link in codemap.test_links:
        test = link["test"]
        target = link["target"]
        if scores.get(target, 0) > 0 and test in scores:
            scores[test] += 12
            reasons[test].append(f"test_for:{target}")
        if scores.get(test, 0) > 0 and target in scores:
            scores[target] += 10
            reasons[target].append(f"covered_by:{test}")

    selected = [
        selected_file_entry(path, score, reasons[path], int(nodes[path].get("estimated_tokens", 1)))
        for path, score in scores.items()
        if score > 0
    ]
    selected.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    if not selected:
        unknowns.append("No files selected by CodeMap selection; add --include or run read-only explore worker.")
    return selected, unknowns


def normalize_include_path(value: str) -> str:
    normalized = Path(value).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def detect_test_commands(root: Path, explicit: list[str]) -> list[str]:
    commands = list(explicit)
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


def render_context_markdown(pack: dict[str, object]) -> str:
    selected = "\n".join(
        f"- `{item['path']}` score={item['score']} ~{item['estimated_tokens']} tok — {item['reason']}"
        for item in pack["selected_files"]
    ) or "- _(none)_"
    symbols = "\n".join(
        f"- `{item['path']}:{item['line']}` {item['kind']} `{item['name']}`"
        for item in pack["relevant_symbols"]
    ) or "- _(none)_"
    tests = "\n".join(f"- `{cmd}`" for cmd in pack["test_commands"]) or "- _(none detected)_"
    unknowns = "\n".join(f"- {item}" for item in pack["unknowns"]) or "- _(none)_"
    cache = pack.get("codemap_cache", {})
    return f"""# RGM CodeMap Context Pack

> Input artifact only. CodeMap selection helps the agent choose context; it is **not** completion proof. `rgm_gate.py` / verifiers still decide FULL_GREEN.

- Task: {pack['task']}
- CodeMap: `{pack['codemap_path']}`
- Selected files: {len(pack['selected_files'])}
- Token budget: {pack['token_budget']['selected_context_tokens']} / {pack['token_budget']['max_context_tokens']} estimated tokens
- CodeMap cache: hits={cache.get('hits', 0)} misses={cache.get('misses', 0)} writes={cache.get('writes', 0)}

## Selection algorithm
{pack['selection_algorithm']['summary']}

## Selected files
{selected}

## Relevant symbols
{symbols}

## Test / verifier commands
{tests}

## Unknowns
{unknowns}

## Scope rule
{pack['scope_rule']}
"""


def build_selection_pack(root: Path, codemap: CodeMap, codemap_path: Path, task: str, includes: list[str], verify: list[str], max_files: int, max_file_bytes: int, max_context_tokens: int) -> dict[str, object]:
    root = root.resolve()
    selected, unknowns = score_selection(codemap, root, task, includes, max_file_bytes=max_file_bytes)
    selected = selected[:max_files]
    selected_tokens = sum(int(item["estimated_tokens"]) for item in selected)
    if selected_tokens > max_context_tokens:
        unknowns.append("Selected CodeMap context exceeds max_context_tokens; lower --max-files or split by lane.")
    if codemap.git.get("status_short"):
        unknowns.append("Git worktree is dirty at CodeMap selection time; diff outside selected/risk files requires ledger justification.")
    node_by_path = {str(f["path"]): f for f in codemap.files}
    selected_paths = {str(item["path"]) for item in selected}
    relevant_symbols = []
    for path in selected_paths:
        node = node_by_path.get(path)
        if not node:
            continue
        for sym in node.get("symbols", [])[:30]:
            relevant_symbols.append({"path": path, **sym})
    risk_files = [
        str(f["path"]) for f in codemap.files
        if str(f["path"]) in selected_paths and f.get("risk_tags")
    ]
    edge_set = [
        edge for edge in codemap.dependency_edges
        if edge["from"] in selected_paths or edge["to"] in selected_paths
    ][:80]
    return {
        "schema_version": "rgm-context-pack.v2",
        "generated_at": now_iso(),
        "task": task,
        "root": str(root),
        "codemap_path": str(codemap_path),
        "codemap_schema_version": codemap.schema_version,
        "codemap_cache": codemap.cache,
        "git": codemap.git,
        "selected_files": selected,
        "relevant_symbols": relevant_symbols[:120],
        "dependency_edges": edge_set,
        "test_links": [l for l in codemap.test_links if l["test"] in selected_paths or l["target"] in selected_paths][:80],
        "test_commands": detect_test_commands(root, verify),
        "risk_files": risk_files[:30],
        "unknowns": unknowns,
        "token_budget": {
            "max_context_tokens": max_context_tokens,
            "selected_context_tokens": selected_tokens,
            "selected_file_count": len(selected),
        },
        "selection_algorithm": {
            "name": "rgm-codemap-selection.v1",
            "summary": "Score task terms in path/content/symbol/import/risk, then expand one dependency/dependent hop and linked tests; explicit includes dominate; token budget caps final pack.",
            "factors": ["explicit_include", "path_terms", "content_terms", "symbol_terms", "import_terms", "risk_terms", "dependency_hop", "dependent_hop", "test_link"],
        },
        "scope_rule": "Edits outside selected_files/risk_files/dependency_edges require a Run Ledger hypothesis and justification; CodeMap context pack is not completion proof.",
        "not_completion_proof": True,
    }


def write_selection_pack(pack: dict[str, object], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "context-pack.json"
    md_path = out_dir / "context-pack.md"
    json_path.write_text(json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_context_markdown(pack), encoding="utf-8")
    return json_path, md_path


def load_codemap(path: Path) -> CodeMap:
    data = json.loads(path.read_text(encoding="utf-8"))
    return CodeMap(
        schema_version=data.get("schema_version", "rgm-codemap.v1"),
        generated_at=data["generated_at"],
        root=data["root"],
        git=data.get("git", {}),
        files=data.get("files", []),
        dependency_edges=data.get("dependency_edges", []),
        symbol_index=data.get("symbol_index", []),
        test_links=data.get("test_links", []),
        cache=data.get("cache", {"enabled": False, "hits": 0, "misses": 0, "writes": 0}),
        pipeline_identity=data.get("pipeline_identity", {}),
        not_completion_proof=bool(data.get("not_completion_proof", True)),
    )


def cache_dir_from_args(out_dir: str, cache_dir: str) -> Path:
    return Path(cache_dir) if cache_dir else Path(out_dir) / "codemap-artifacts"


def cmd_build(args: argparse.Namespace) -> int:
    codemap = build_codemap(
        Path(args.root),
        max_file_bytes=args.max_file_bytes,
        cache_dir=None if args.no_cache else cache_dir_from_args(args.out_dir, args.cache_dir),
        use_cache=not args.no_cache,
        refresh_cache=args.refresh_cache,
    )
    json_path, md_path = write_codemap(codemap, Path(args.out_dir))
    if args.print_json:
        print(json.dumps(asdict(codemap), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("<!--RGM_CODEMAP_BEGIN-->")
        print(f"CODEMAP_JSON={json_path}")
        print(f"CODEMAP_MD={md_path}")
        print(f"FILES={len(codemap.files)}")
        print(f"SYMBOLS={len(codemap.symbol_index)}")
        print(f"DEPENDENCY_EDGES={len(codemap.dependency_edges)}")
        print(f"CACHE_HITS={codemap.cache.get('hits', 0)}")
        print(f"CACHE_MISSES={codemap.cache.get('misses', 0)}")
        print(f"CACHE_WRITES={codemap.cache.get('writes', 0)}")
        print("CONTENT_ADDRESSED_CACHE=true")
        print("NOT_COMPLETION_PROOF=true")
        print("<!--RGM_CODEMAP_END-->")
    return 0


def cmd_select(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    codemap_path = Path(args.codemap) if args.codemap else Path(args.out_dir) / "codemap.json"
    if not codemap_path.exists():
        codemap = build_codemap(
            root,
            max_file_bytes=args.max_file_bytes,
            cache_dir=None if args.no_cache else cache_dir_from_args(args.out_dir, args.cache_dir),
            use_cache=not args.no_cache,
            refresh_cache=args.refresh_cache,
        )
        codemap_path, _ = write_codemap(codemap, Path(args.out_dir))
    else:
        codemap = load_codemap(codemap_path)
    pack = build_selection_pack(
        root=root,
        codemap=codemap,
        codemap_path=codemap_path,
        task=args.task,
        includes=args.include or [],
        verify=args.verify or [],
        max_files=args.max_files,
        max_file_bytes=args.max_file_bytes,
        max_context_tokens=args.max_context_tokens,
    )
    json_path, md_path = write_selection_pack(pack, Path(args.out_dir))
    if args.print_json:
        print(json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("<!--RGM_CODEMAP_CONTEXT_BEGIN-->")
        print(f"CONTEXT_PACK_JSON={json_path}")
        print(f"CONTEXT_PACK_MD={md_path}")
        print(f"CODEMAP_JSON={codemap_path}")
        print(f"SELECTED_FILES={len(pack['selected_files'])}")
        print(f"TEST_COMMANDS={len(pack['test_commands'])}")
        print(f"UNKNOWNS={len(pack['unknowns'])}")
        print(f"CACHE_HITS={codemap.cache.get('hits', 0)}")
        print(f"CACHE_MISSES={codemap.cache.get('misses', 0)}")
        print("NOT_COMPLETION_PROOF=true")
        print("<!--RGM_CODEMAP_CONTEXT_END-->")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build CodeMap and select red-green context")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="generate .rgm/codemap.json and .md")
    build.add_argument("--root", default=".")
    build.add_argument("--out-dir", default=".rgm")
    build.add_argument("--cache-dir", default="", help="artifact cache dir; default: <out-dir>/codemap-artifacts")
    build.add_argument("--no-cache", action="store_true")
    build.add_argument("--refresh-cache", action="store_true")
    build.add_argument("--max-file-bytes", type=int, default=200_000)
    build.add_argument("--print-json", action="store_true")
    build.set_defaults(func=cmd_build)

    select = sub.add_parser("select", help="select context using a CodeMap")
    select.add_argument("--root", default=".")
    select.add_argument("--task", required=True)
    select.add_argument("--codemap", default="", help="existing codemap path; auto-builds when absent")
    select.add_argument("--out-dir", default=".rgm")
    select.add_argument("--cache-dir", default="", help="artifact cache dir; default: <out-dir>/codemap-artifacts")
    select.add_argument("--no-cache", action="store_true")
    select.add_argument("--refresh-cache", action="store_true")
    select.add_argument("--include", action="append", default=[])
    select.add_argument("--verify", action="append", default=[])
    select.add_argument("--max-files", type=int, default=24)
    select.add_argument("--max-file-bytes", type=int, default=200_000)
    select.add_argument("--max-context-tokens", type=int, default=24_000)
    select.add_argument("--print-json", action="store_true")
    select.set_defaults(func=cmd_select)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

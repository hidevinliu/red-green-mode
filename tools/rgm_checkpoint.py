#!/usr/bin/env python3
"""File checkpoint helper for red-green-mode non-git or dirty workspaces.

Creates named snapshots of explicit file paths under .rgm-checkpoints/.
It intentionally requires explicit --paths to avoid surprising bulk copies.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = ".rgm-checkpoints"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def checkpoint_dir(workspace: Path, name: str) -> Path:
    safe = name.replace("/", "_").replace("..", "_")
    return workspace / ROOT_DIR / safe


def manifest_path(cp: Path) -> Path:
    return cp / "manifest.json"


def rel_to_workspace(workspace: Path, path: Path) -> Path:
    try:
        return path.resolve().relative_to(workspace.resolve())
    except ValueError as exc:
        raise SystemExit(f"Path is outside workspace: {path}") from exc


def load_manifest(cp: Path) -> dict[str, Any]:
    mp = manifest_path(cp)
    if not mp.exists():
        raise SystemExit(f"Missing checkpoint manifest: {mp}")
    return json.loads(mp.read_text(encoding="utf-8"))


def cmd_create(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).resolve()
    cp = checkpoint_dir(workspace, args.name)
    if cp.exists() and not args.force:
        raise SystemExit(f"Checkpoint exists: {cp} (use --force)")
    cp.mkdir(parents=True, exist_ok=True)
    files = []
    for raw in args.paths:
        src = (workspace / raw).resolve()
        if not src.exists():
            if args.allow_missing:
                # Normalize like the existing-file branch (store a workspace-
                # relative posix path, not the raw arg) so restore treats
                # missing and present files consistently.
                rel = rel_to_workspace(workspace, src)
                files.append({"path": rel.as_posix(), "exists": False})
                continue
            raise SystemExit(f"Cannot checkpoint missing file: {raw}")
        if not src.is_file():
            raise SystemExit(f"Only files can be checkpointed: {raw}")
        rel = rel_to_workspace(workspace, src)
        dest = cp / "files" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        files.append({"path": rel.as_posix(), "exists": True})
    manifest = {"name": args.name, "workspace": str(workspace), "created_at": now_iso(), "files": files}
    manifest_path(cp).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"created checkpoint {args.name} files={len(files)}")


def cmd_restore(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).resolve()
    cp = checkpoint_dir(workspace, args.name)
    manifest = load_manifest(cp)
    backup_root = cp / "pre-restore"
    do_backup = not args.no_backup
    restored = 0
    backed_up = 0
    for item in manifest.get("files", []):
        rel = Path(item["path"])
        # Defense in depth: a corrupted/tampered manifest must not write
        # outside the workspace (pathlib drops the left side on an absolute
        # join, and ".." could climb out). create-time validation already
        # normalizes paths; re-check here so restore is safe on its own.
        if rel.is_absolute() or ".." in rel.parts:
            raise SystemExit(f"Refusing to restore unsafe path from manifest: {item['path']}")
        target = workspace / rel
        # Safety net: snapshot the CURRENT (possibly dirty) file before
        # overwriting, so a wrong restore is itself recoverable.
        if do_backup and target.exists() and target.is_file():
            bdest = backup_root / rel
            bdest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, bdest)
            backed_up += 1
        if item.get("exists"):
            src = cp / "files" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            restored += 1
        elif target.exists() and args.delete_missing:
            target.unlink()
            restored += 1
    suffix = f" backed_up={backed_up} (in {backup_root})" if backed_up else ""
    print(f"restored checkpoint {args.name} files={restored}{suffix}")


def cmd_list(args: argparse.Namespace) -> None:
    workspace = Path(args.workspace).resolve()
    root = workspace / ROOT_DIR
    if not root.exists():
        print("no checkpoints")
        return
    for mp in sorted(root.glob("*/manifest.json")):
        data = json.loads(mp.read_text(encoding="utf-8"))
        print(f"{data.get('name')} files={len(data.get('files', []))} created_at={data.get('created_at')}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Checkpoint helper for red-green-mode")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create")
    create.add_argument("--workspace", default=".")
    create.add_argument("--name", required=True)
    create.add_argument("--paths", nargs="+", required=True)
    create.add_argument("--allow-missing", action="store_true")
    create.add_argument("--force", action="store_true")
    create.set_defaults(func=cmd_create)

    restore = sub.add_parser("restore")
    restore.add_argument("--workspace", default=".")
    restore.add_argument("--name", required=True)
    restore.add_argument("--delete-missing", action="store_true")
    restore.add_argument("--no-backup", action="store_true",
                         help="skip snapshotting current files before overwrite")
    restore.set_defaults(func=cmd_restore)

    ls = sub.add_parser("list")
    ls.add_argument("--workspace", default=".")
    ls.set_defaults(func=cmd_list)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

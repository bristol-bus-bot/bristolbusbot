#!/usr/bin/env python3
"""Verify a staged release manifest and its payload hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath


SAFE = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
FORBIDDEN_NAMES = {".env", "timetable.db", "live.db", "audit.db", "app_data.db"}


def verify(root: Path, component: str, release: str) -> None:
    if not SAFE.fullmatch(component) or not SAFE.fullmatch(release):
        raise RuntimeError("unsafe component or release identifier")
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("release root must be a real directory")
    manifest_path = root / "release.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("component") != component or manifest.get("release") != release:
        raise RuntimeError("release manifest identity mismatch")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise RuntimeError("release manifest has no files")
    actual: set[str] = set()
    for path in root.rglob("*"):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeError(f"release contains a symlink: {relative}")
        if stat.S_ISREG(info.st_mode):
            if path.name in FORBIDDEN_NAMES:
                raise RuntimeError(f"release contains a forbidden file: {relative}")
            if relative != "release.json":
                actual.add(relative)
    if actual != set(files):
        raise RuntimeError("release payload does not exactly match its manifest")
    for name, expected_hash in files.items():
        pure = PurePosixPath(name)
        if pure.is_absolute() or ".." in pure.parts or pure.name in FORBIDDEN_NAMES:
            raise RuntimeError(f"unsafe manifested path: {name}")
        path = root.joinpath(*pure.parts)
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"manifested path is not a regular file: {name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected_hash:
            raise RuntimeError(f"release hash mismatch: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("component")
    parser.add_argument("release")
    args = parser.parse_args()
    verify(args.root, args.component, args.release)
    print(f"{args.component}/{args.release}: release manifest verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

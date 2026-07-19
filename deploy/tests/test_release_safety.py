import hashlib
import json
import stat
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verify_release import verify


def manifest(root: Path, component: str = "site", release: str = "release-1") -> None:
    files = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file() and path.name != "release.json"
    }
    (root / "release.json").write_text(json.dumps({
        "schema": 1, "component": component, "release": release, "files": files,
    }), encoding="utf-8")


def test_release_requires_an_exact_hash_manifest(tmp_path):
    (tmp_path / "app.py").write_text("safe", encoding="utf-8")
    manifest(tmp_path)
    verify(tmp_path, "site", "release-1")

    (tmp_path / "app.py").write_text("tampered", encoding="utf-8")
    with pytest.raises(RuntimeError, match="hash mismatch"):
        verify(tmp_path, "site", "release-1")


def test_release_rejects_unmanifested_and_durable_files(tmp_path):
    (tmp_path / "app.py").write_text("safe", encoding="utf-8")
    manifest(tmp_path)
    (tmp_path / "extra.py").write_text("not manifested", encoding="utf-8")
    with pytest.raises(RuntimeError, match="exactly match"):
        verify(tmp_path, "site", "release-1")

    (tmp_path / "extra.py").unlink()
    (tmp_path / ".env").write_text("SECRET=hidden", encoding="utf-8")
    with pytest.raises(RuntimeError, match="forbidden"):
        verify(tmp_path, "site", "release-1")


def test_release_rejects_symlinks_when_supported(tmp_path):
    (tmp_path / "app.py").write_text("safe", encoding="utf-8")
    manifest(tmp_path)
    link = tmp_path / "linked.py"
    try:
        link.symlink_to(tmp_path / "app.py")
    except OSError:
        pytest.skip("test account cannot create Windows symlinks")
    with pytest.raises(RuntimeError, match="symlink"):
        verify(tmp_path, "site", "release-1")

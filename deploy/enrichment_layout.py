#!/usr/bin/env python3
"""Validate and safely seed the Pi-owned enrichment data boundary."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path


DURABLE_DIRECTORY_TEXT = "/var/lib/bristolbusbot/enrichment"
DURABLE_DIRECTORY = Path(DURABLE_DIRECTORY_TEXT)
BACKUP_CONFIG = Path("/etc/bristolbusbot/backup.json")
MODEL_CONTEXT = "model-context.json"
MAXIMUM_MODEL_CONTEXT_BYTES = 1024 * 1024
MAXIMUM_MODEL_CONTEXT_RECORDS = 1000
MAXIMUM_CONTEXT_ADDITIONS = 100
MAXIMUM_CONTEXT_CORRECTIONS = 20
SPECS: dict[str, type] = {
    "fbribuses.json": list,
    "stop_localities.json": dict,
    "stop_enrichment.json": dict,
    "local_flavour.json": dict,
    "route_details.json": dict,
    "bus-descriptions.json": dict,
    "waiting-descriptions.json": dict,
    "depot-descriptions.json": dict,
    "model-context.json": dict,
}


class EnrichmentLayoutError(RuntimeError):
    """The durable enrichment boundary is absent or unsafe."""


def _directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise EnrichmentLayoutError(f"{label} is absent: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise EnrichmentLayoutError(f"{label} is unsafe: {path}")
    return info


def _regular_file(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise EnrichmentLayoutError(f"{label} is absent: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise EnrichmentLayoutError(f"{label} is not a regular file: {path}")
    return info


def validate_file(path: Path, expected: type) -> dict[str, object]:
    _regular_file(path, "enrichment file")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise EnrichmentLayoutError(
            f"enrichment JSON is unreadable or invalid: {path.name}") from exc
    if not isinstance(value, expected) or not value:
        raise EnrichmentLayoutError(
            f"enrichment JSON has the wrong shape or is empty: {path.name}")
    return {
        "bytes": len(raw),
        "records": len(value),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def validate_directory(directory: Path = DURABLE_DIRECTORY) -> dict[str, dict]:
    _directory(directory, "enrichment directory")
    return {
        name: validate_file(directory / name, expected)
        for name, expected in SPECS.items()
    }


def _model_context(path: Path) -> dict[str, str]:
    info = _regular_file(path, "model context")
    if info.st_size <= 0 or info.st_size > MAXIMUM_MODEL_CONTEXT_BYTES:
        raise EnrichmentLayoutError("model context is empty or too large")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise EnrichmentLayoutError("model context is invalid JSON") from exc
    if not isinstance(value, dict) or not value \
            or len(value) > MAXIMUM_MODEL_CONTEXT_RECORDS:
        raise EnrichmentLayoutError("model context has the wrong shape or size")
    for key, context in value.items():
        if not isinstance(key, str) or key != key.strip() \
                or not 1 <= len(key) <= 120:
            raise EnrichmentLayoutError("model context has an invalid model name")
        if not isinstance(context, str) \
                or context != " ".join(context.split()) \
                or not 20 <= len(context) <= 500:
            raise EnrichmentLayoutError(
                f"model context has an invalid entry for {key}")
    return value


def sync_model_context(source: Path | list[Path] | tuple[Path, ...],
                       destination: Path, *, uid: int, gid: int) -> str:
    """Atomically sync the reviewed context without allowing a broad rewrite."""
    sources = (source,) if isinstance(source, Path) else tuple(source)
    origin = next((item / MODEL_CONTEXT for item in reversed(sources)
                   if (item / MODEL_CONTEXT).exists()
                   or (item / MODEL_CONTEXT).is_symlink()), None)
    if origin is None:
        raise EnrichmentLayoutError("model context source is absent")
    target = destination / MODEL_CONTEXT
    candidate = _model_context(origin)
    live = _model_context(target)
    removed = sorted(set(live) - set(candidate))
    if removed:
        raise EnrichmentLayoutError(
            f"model context would remove {len(removed)} existing entries")
    additions = sorted(set(candidate) - set(live))
    corrections = sorted(
        key for key in set(candidate) & set(live)
        if candidate[key] != live[key])
    if len(additions) > MAXIMUM_CONTEXT_ADDITIONS:
        raise EnrichmentLayoutError("model context adds too many entries at once")
    if len(corrections) > MAXIMUM_CONTEXT_CORRECTIONS:
        raise EnrichmentLayoutError(
            "model context changes too many existing entries at once")
    if not additions and not corrections:
        return "preserved"
    _atomic_write(
        target, origin.read_bytes(), uid=uid, gid=gid, mode=0o640)
    if _model_context(target) != candidate:
        raise EnrichmentLayoutError("model context did not sync exactly")
    return "updated"


def _atomic_write(path: Path, raw: bytes, *, uid: int, gid: int,
                  mode: int) -> None:
    candidate = path.with_name(f".{path.name}.migration")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if hasattr(os, "chown"):
            os.chown(candidate, uid, gid)
        os.chmod(candidate, mode)
        os.replace(candidate, path)
        if os.name != "nt":
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_descriptor = os.open(path.parent, directory_flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    finally:
        candidate.unlink(missing_ok=True)


def seed_missing(source: Path | list[Path] | tuple[Path, ...],
                 destination: Path, *, uid: int,
                 gid: int) -> dict[str, str]:
    """Copy only absent files; existing durable authority is never replaced."""
    _directory(destination, "enrichment directory")
    sources = (source,) if isinstance(source, Path) else tuple(source)
    if not sources:
        raise EnrichmentLayoutError("at least one migration source is required")
    for item in sources:
        _directory(item, "migration source")
    status: dict[str, str] = {}
    for name, expected in SPECS.items():
        target = destination / name
        if target.exists() or target.is_symlink():
            validate_file(target, expected)
            status[name] = "preserved"
            continue
        origin = next((item / name for item in sources
                       if (item / name).exists() or (item / name).is_symlink()),
                      None)
        if origin is None:
            raise EnrichmentLayoutError(
                f"enrichment seed source is absent: {name}")
        validate_file(origin, expected)
        _atomic_write(target, origin.read_bytes(), uid=uid, gid=gid, mode=0o640)
        validate_file(target, expected)
        status[name] = "seeded"
    return status


def ensure_backup_include(path: Path = BACKUP_CONFIG) -> bool:
    """Make the durable enrichment directory a required backup source."""
    info = _regular_file(path, "backup configuration")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnrichmentLayoutError("backup configuration is invalid") from exc
    if not isinstance(config, dict) or not isinstance(config.get("paths"), list):
        raise EnrichmentLayoutError("backup configuration has no paths list")
    matches = [item for item in config["paths"]
               if isinstance(item, dict)
               and item.get("path") == DURABLE_DIRECTORY_TEXT]
    if len(matches) > 1:
        raise EnrichmentLayoutError(
            "backup configuration repeats the enrichment directory")
    changed = False
    if matches:
        if matches[0].get("required") is not True:
            matches[0]["required"] = True
            changed = True
    else:
        config["paths"].append({
            "name": "enrichment-state",
            "path": DURABLE_DIRECTORY_TEXT,
            "required": True,
        })
        changed = True
    if changed:
        raw = (json.dumps(config, indent=2) + "\n").encode("utf-8")
        _atomic_write(
            path, raw, uid=info.st_uid, gid=info.st_gid,
            mode=stat.S_IMODE(info.st_mode))
    return changed


def migrate(source: Path | list[Path] | tuple[Path, ...],
            destination: Path, backup_config: Path, *,
            owner: str) -> dict[str, object]:
    import pwd  # POSIX-only production migration; validation remains portable.
    try:
        account = pwd.getpwnam(owner)
    except KeyError as exc:
        raise EnrichmentLayoutError("deployment account does not exist") from exc
    seeded = seed_missing(
        source, destination, uid=account.pw_uid, gid=account.pw_gid)
    seeded[MODEL_CONTEXT] = sync_model_context(
        source, destination, uid=account.pw_uid, gid=account.pw_gid)
    backup_changed = ensure_backup_include(backup_config)
    return {
        "files": seeded,
        "backup_changed": backup_changed,
        "validated": validate_directory(destination),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--directory", type=Path, default=DURABLE_DIRECTORY)
    validate.add_argument("--quiet", action="store_true")
    migration = subparsers.add_parser("migrate")
    migration.add_argument("--source", type=Path, action="append", required=True)
    migration.add_argument("--destination", type=Path,
                           default=DURABLE_DIRECTORY)
    migration.add_argument("--backup-config", type=Path, default=BACKUP_CONFIG)
    migration.add_argument("--owner", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            result = validate_directory(args.directory)
            if not args.quiet:
                print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(json.dumps(migrate(
                args.source, args.destination, args.backup_config,
                owner=args.owner), indent=2, sort_keys=True))
        return 0
    except (OSError, EnrichmentLayoutError) as exc:
        parser.exit(1, f"enrichment layout rejected: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

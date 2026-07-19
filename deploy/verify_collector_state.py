#!/usr/bin/env python3
"""Read-only integrity and freshness gate for production collector state."""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_STATE = Path("/var/lib/bristolbusbot/collector")


def quick_check(path: Path) -> None:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        connection.close()
    if result != "ok":
        raise RuntimeError(f"{path}: SQLite quick_check returned {result!r}")
    print(f"{path}: quick_check ok")


def poll_age(path: Path) -> float:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT last_success_at FROM poller_status WHERE name='siri_vm'"
        ).fetchone()
    finally:
        connection.close()
    if not row or not row[0]:
        raise RuntimeError("live.db has no successful SIRI-VM poll")
    seen = datetime.fromisoformat(row[0])
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - seen.astimezone(timezone.utc)).total_seconds()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--max-poll-age", type=float, default=120.0)
    args = parser.parse_args()
    live = args.state / "live.db"
    quick_check(live)
    quick_check(args.state / "audit.db")
    age = poll_age(live)
    if age < 0 or age > args.max_poll_age:
        raise RuntimeError(f"latest SIRI-VM success is stale: {age:.1f}s")
    print(f"SIRI-VM success age: {age:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

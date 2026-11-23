#!/usr/bin/env python3
"""Bring the PC's daily data up to yesterday from the private store (#152).

The Pi's nightly export pushes one branch per export to a private GitHub
repository. This downloads any not yet on the PC, using your own GitHub
sign-in (the one `git` and `gh` already use). Nothing connects to the Pi.

    python deploy\\get_daily_exports.py               # download new days
    python deploy\\get_daily_exports.py --combine     # and rebuild readings-combined.db

Files land in C:\\dev\\bbb-research\\daily\\ under readings\\, rare-workings\\,
writer-drafts\\ and reports\\. Each report pair is in its own timestamped
folder. --combine rebuilds daily\\readings-combined.db from every
readings file, one table per source table plus an export_days table.
"""
from __future__ import annotations

import argparse
import gzip
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_REMOTE = "https://github.com/bristol-bus-bot/bristolbusbot-daily-data.git"
DEFAULT_ROOT = Path(r"C:\dev\bbb-research\daily")
BRANCH_RE = re.compile(
    r"^(readings|rare-workings|writer-drafts|reports)/(\d{4}-\d{2}-\d{2}(?:T\d{4}Z)?)$")
SUFFIX = {"readings": ".sqlite.gz", "rare-workings": ".jsonl.gz", "writer-drafts": ".jsonl.gz"}
REPORT_NAMES = ("collector-anomaly.json", "data-health.json")
FETCH_BATCH = 50


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git {args[0]} failed: {result.stderr.strip()[-400:]}")
    return result.stdout


def git_bytes(*args: str, cwd: Path) -> bytes:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"git {args[0]} failed: {result.stderr.decode(errors='replace')[-400:]}")
    return result.stdout


def remote_branches(remote: str) -> list[str]:
    heads = []
    for line in git("ls-remote", "--heads", remote).splitlines():
        name = line.split("refs/heads/", 1)[-1]
        if BRANCH_RE.match(name):
            heads.append(name)
    return sorted(heads)


def local_paths(root: Path, branch: str) -> tuple[Path, ...]:
    part, label = BRANCH_RE.match(branch).groups()
    if part == "reports":
        return tuple(root / part / label / name for name in REPORT_NAMES)
    return (root / part / f"{label}{SUFFIX[part]}",)


def download(remote: str, root: Path) -> list[Path]:
    wanted = [b for b in remote_branches(remote)
              if not all(path.exists() for path in local_paths(root, b))]
    if not wanted:
        return []
    cache = root / ".cache.git"
    if not cache.exists():
        git("init", "-q", "--bare", str(cache))
    saved = []
    for start in range(0, len(wanted), FETCH_BATCH):
        batch = wanted[start:start + FETCH_BATCH]
        git("fetch", "-q", "--depth", "1", "--no-tags", remote,
            *[f"+refs/heads/{b}:refs/daily/{b}" for b in batch], cwd=cache)
        for branch in batch:
            targets = local_paths(root, branch)
            blobs = [git_bytes("show", f"refs/daily/{branch}:{target.relative_to(root).as_posix()}",
                               cwd=cache) for target in targets]
            for target, blob in zip(targets, blobs, strict=True):
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(target.name + ".part")
                partial.write_bytes(blob)
                partial.replace(target)
                saved.append(target)
    return saved


def combine(root: Path) -> Path:
    files = sorted((root / "readings").glob("*.sqlite.gz"))
    output = root / "readings-combined.db"
    partial = output.with_name(output.name + ".part")
    partial.unlink(missing_ok=True)
    combined = sqlite3.connect(partial)
    combined.execute("CREATE TABLE export_days (service_date TEXT PRIMARY KEY, file TEXT)")
    with tempfile.TemporaryDirectory(prefix="bbb-daily-") as work:
        for file in files:
            plain = Path(work) / "day.sqlite"
            with gzip.open(file, "rb") as source, plain.open("wb") as out:
                shutil.copyfileobj(source, out)
            combined.execute("ATTACH DATABASE ? AS day", (str(plain),))
            day = combined.execute(
                "SELECT value FROM day.export_info WHERE key='service_date'").fetchone()[0]
            for name, sql in combined.execute(
                    "SELECT name, sql FROM day.sqlite_master WHERE type='table' "
                    "AND name != 'export_info'").fetchall():
                columns = [r[1] for r in combined.execute(f'PRAGMA day.table_info("{name}")')]
                if not combined.execute(
                        "SELECT 1 FROM main.sqlite_master WHERE name=?", (name,)).fetchone():
                    combined.execute(sql)
                else:
                    have = {r[1] for r in combined.execute(f'PRAGMA main.table_info("{name}")')}
                    for column in columns:
                        if column not in have:
                            combined.execute(f'ALTER TABLE "{name}" ADD COLUMN "{column}"')
                quoted = ", ".join(f'"{c}"' for c in columns)
                combined.execute(
                    f'INSERT INTO main."{name}" ({quoted}) SELECT {quoted} FROM day."{name}"')
            combined.execute("INSERT OR REPLACE INTO export_days VALUES (?, ?)", (day, file.name))
            combined.commit()
            combined.execute("DETACH DATABASE day")
    for index in (
            "CREATE INDEX IF NOT EXISTS idx_obs_date_route ON timepoint_observations"
            "(service_date, operator, route)",
            "CREATE INDEX IF NOT EXISTS idx_expected_date_route ON expected_trips"
            "(service_date, operator, route)"):
        try:
            combined.execute(index)
        except sqlite3.OperationalError:
            pass
    combined.commit()
    combined.close()
    partial.replace(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--combine", action="store_true",
                        help="rebuild readings-combined.db from every readings file")
    args = parser.parse_args(argv)
    args.root.mkdir(parents=True, exist_ok=True)
    saved = download(args.remote, args.root)
    for path in saved:
        print("saved", path)
    readings = sorted(p.name for p in (args.root / "readings").glob("*.sqlite.gz"))
    print(f"{len(saved)} new file(s). Readings on the PC: "
          + (f"{readings[0][:10]} to {readings[-1][:10]} ({len(readings)} days)" if readings else "none"))
    if args.combine:
        print("combined copy:", combine(args.root))
    return 0


if __name__ == "__main__":
    sys.exit(main())

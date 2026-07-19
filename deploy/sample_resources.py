#!/usr/bin/env python3
"""Sample per-unit RSS; report p95 only after a seven-day observation span."""
from __future__ import annotations

import argparse
import csv
import math
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


UNITS = ("bbb-site.service", "bbb-collector.service", "bbb-bot.service",
         "bbb-tunnel.service")
DEFAULT_OUTPUT = Path("/var/lib/bristolbusbot/monitoring/resource-samples.csv")


def pids_for(unit: str) -> list[int]:
    cgroup = Path("/sys/fs/cgroup/system.slice") / unit / "cgroup.procs"
    try:
        return [int(item) for item in cgroup.read_text().split()]
    except (OSError, ValueError):
        result = subprocess.run(
            ["systemctl", "show", unit, "-p", "MainPID", "--value"],
            capture_output=True, text=True, check=False)
        return [int(result.stdout)] if result.stdout.strip().isdigit() else []


def rss_kib(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def sample(output: Path) -> None:
    import fcntl
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    with output.open("a+", encoding="utf-8", newline="") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        empty = handle.tell() == 0
        writer = csv.writer(handle)
        if empty:
            writer.writerow(("timestamp_utc", "unit", "rss_kib", "tasks"))
        stamp = datetime.now(timezone.utc).isoformat()
        for unit in UNITS:
            pids = pids_for(unit)
            writer.writerow((stamp, unit, sum(rss_kib(pid) for pid in pids), len(pids)))


def report(output: Path, minimum_days: float) -> int:
    rows: dict[str, list[tuple[datetime, int]]] = defaultdict(list)
    with output.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows[row["unit"]].append(
                (datetime.fromisoformat(row["timestamp_utc"]), int(row["rss_kib"])))
    incomplete = False
    for unit in UNITS:
        values = sorted(rows.get(unit, []), key=lambda item: item[1])
        if not values:
            print(f"{unit}: no samples")
            incomplete = True
            continue
        span = (max(item[0] for item in values) - min(item[0] for item in values)).total_seconds() / 86400
        p95 = values[max(0, math.ceil(len(values) * 0.95) - 1)][1]
        if span < minimum_days:
            print(f"{unit}: {len(values)} samples over {span:.2f}d; need {minimum_days:.0f}d")
            incomplete = True
            continue
        high_mib = math.ceil((p95 / 1024 * 1.5) / 16) * 16
        max_mib = math.ceil((p95 / 1024 * 2.0) / 16) * 16
        print(f"{unit}: p95={p95 / 1024:.1f}MiB MemoryHigh={high_mib}M MemoryMax={max_mib}M")
    return 2 if incomplete else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--minimum-days", type=float, default=7.0)
    args = parser.parse_args()
    if args.report:
        return report(args.output, args.minimum_days)
    sample(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

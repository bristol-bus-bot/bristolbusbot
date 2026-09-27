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
HEAVY_UNITS = ('bbb-backup.service', 'bbb-timetable-shadow-auto.service',
               'bbb-audit-rollup.service')
MAX_SAMPLE_BYTES = 8 * 1024 * 1024
DEFAULT_OUTPUT = Path("/var/lib/bristolbusbot/monitoring/resource-samples.csv")


def pids_for(unit: str) -> list[int]:
    cgroup = Path("/sys/fs/cgroup/system.slice") / unit / "cgroup.procs"
    try:
        return [int(item) for item in cgroup.read_text().split()]
    except (OSError, ValueError):
        result = subprocess.run(
            ["systemctl", "show", unit, "-p", "MainPID", "--value"],
            capture_output=True, text=True, check=False)
        pid = int(result.stdout) if result.stdout.strip().isdigit() else 0
        return [pid] if pid > 0 else []


def rss_kib(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def host_values(meminfo: str, loadavg: str) -> list[int | float]:
    values = {line.split(':')[0]: int(line.split()[1])
              for line in meminfo.splitlines() if ':' in line}
    available = values['MemAvailable']
    return [values['MemTotal'] - available, available,
            values['SwapTotal'] - values['SwapFree'], float(loadavg.split()[0])]


def append_rows(output: Path, header, rows) -> None:
    import fcntl
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    # A separate lock remains valid when the data pathname rotates.
    with output.with_name(output.name + '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if output.exists() and output.stat().st_size >= MAX_SAMPLE_BYTES:
            output.replace(output.with_name(output.name + '.1'))
        with output.open('a+', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            if handle.tell() == 0:
                writer.writerow(header)
            writer.writerows(rows)


def sample(output: Path) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    rows = []
    for unit in (*UNITS, *HEAVY_UNITS):
        pids = pids_for(unit)
        if unit in HEAVY_UNITS and not pids:
            continue
        rows.append((stamp, unit, sum(rss_kib(pid) for pid in pids), len(pids)))
    append_rows(output, ('timestamp_utc', 'unit', 'rss_kib', 'tasks'), rows)
    try:
        values = host_values(Path('/proc/meminfo').read_text(),
                             Path('/proc/loadavg').read_text())
    except (OSError, ValueError, KeyError, IndexError):
        values = ['', '', '', '']
    try:
        throttle = subprocess.run(['vcgencmd', 'get_throttled'], timeout=5,
                                  capture_output=True, text=True, check=False)
        throttled = throttle.stdout.strip() if throttle.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        throttled = ''
    append_rows(output.with_name(output.stem + '-host.csv'),
                ('timestamp_utc', 'used_kib', 'available_kib', 'swap_used_kib',
                 'load1', 'throttled'), [(stamp, *values, throttled)])


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

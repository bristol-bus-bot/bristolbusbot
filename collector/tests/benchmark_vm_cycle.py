"""Small repeatable benchmark for the collector's ordinary SIRI-VM path.

Run from the repository root after activating the collector development
environment:

    python collector/tests/benchmark_vm_cycle.py --iterations 1000

This uses the same in-memory timetable and canned normal reading as the cycle
tests. It is intentionally not a pass/fail test; record the result before and
after collector changes on the same machine.
"""
from __future__ import annotations

import argparse
import json
import time

from collector import audit_db, live_db
from collector.config import Config
from collector.run import vm_cycle
from fixture_gtfs import build
from test_run import BristolBoxBoundary, LDN, NOW, VM_FEED


def measure(iterations: int, warmup: int) -> dict[str, float | int]:
    timetable = build()
    live_connection = live_db.connect()
    audit_connection = audit_db.connect()
    boundary = BristolBoxBoundary()
    config = Config(bods_api_key="benchmark")

    def cycle() -> None:
        result = vm_cycle(
            lambda: VM_FEED, timetable.cursor(), live_connection,
            audit_connection, boundary, config, LDN, now_utc=NOW)
        if not result["ok"] or result["matched"] != 1:
            raise RuntimeError(f"benchmark cycle failed: {result}")

    for _ in range(warmup):
        cycle()
    started = time.perf_counter()
    for _ in range(iterations):
        cycle()
    elapsed_s = time.perf_counter() - started
    return {
        "iterations": iterations,
        "elapsed_s": round(elapsed_s, 6),
        "milliseconds_per_poll": round(elapsed_s * 1000 / iterations, 4),
        "polls_per_second": round(iterations / elapsed_s, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=50)
    args = parser.parse_args()
    if args.iterations < 1 or args.warmup < 0:
        parser.error("iterations must be positive and warmup cannot be negative")
    print(json.dumps(measure(args.iterations, args.warmup), sort_keys=True))


if __name__ == "__main__":
    main()

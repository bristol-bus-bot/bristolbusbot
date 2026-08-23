"""Run Gunicorn and prove the website can answer a real local request."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from urllib.request import Request, urlopen

from .systemd_watchdog import notify_watchdog


def probe(url: str, *, timeout: float = 3.0) -> bool:
    request = Request(url, headers={"User-Agent": "bbb-systemd-watchdog/1"})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status == 200 and response.read(16) == b"ok"
    except Exception:  # noqa: BLE001 - every probe failure must fail closed
        return False


def watchdog_interval(environment: dict[str, str] | None = None) -> float:
    environment = os.environ if environment is None else environment
    try:
        timeout = int(environment.get("WATCHDOG_USEC", "120000000")) / 1_000_000
    except ValueError:
        timeout = 120.0
    return min(30.0, max(1.0, timeout / 3))


def supervise(
    command: Sequence[str],
    url: str,
    *,
    interval: float,
    probe_site: Callable[[str], bool] = probe,
    report_progress: Callable[[], bool] = notify_watchdog,
    process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> int:
    process = process_factory(list(command))
    while process.poll() is None:
        if probe_site(url):
            report_progress()
        try:
            return process.wait(timeout=interval)
        except subprocess.TimeoutExpired:
            pass
    return int(process.returncode or 0)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--url", required=True)
    result.add_argument("command", nargs=argparse.REMAINDER)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser().error("a child command is required after --")
    return supervise(command, args.url, interval=watchdog_interval())


if __name__ == "__main__":
    sys.exit(main())

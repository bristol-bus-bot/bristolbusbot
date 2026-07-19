#!/usr/bin/env python3
"""Check that the collector is still producing fresh observations."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

LIVE_DB = Path(os.getenv(
    "BBB_LIVE_DB", "/var/lib/bristolbusbot/collector/live.db"))
WEBHOOK_CONF = Path.home() / ".config" / "busbot-alerts" / "webhook"
STALE_MARKER = Path("/var/lib/bristolbusbot/monitoring/stale-alerted")
HEALTHCHECK_ENV = "BBB_COLLECTOR_HEALTHCHECK_URL"


def slack(text: str) -> None:
    """Best-effort alerting; a monitoring failure must not hide its output."""
    print(text)
    try:
        url = WEBHOOK_CONF.read_text().splitlines()[0].strip()
    except OSError:
        print("notify: webhook config missing — not delivered", file=sys.stderr)
        return
    request = urllib.request.Request(
        url,
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=10).read()
    except Exception as exc:  # noqa: BLE001 - monitoring remains best effort
        print(f"notify: post failed: {exc}", file=sys.stderr)


def last_success_age_minutes(
        db_path: Path = LIVE_DB,
        now: datetime | None = None) -> float | None:
    if not db_path.exists():
        return None
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT last_success_at FROM poller_status WHERE name='siri_vm'"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if not row or not row[0]:
        return None
    last = datetime.fromisoformat(row[0])
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - last.astimezone(timezone.utc)).total_seconds() / 60


def healthcheck_target(base_url: str, state: str, run_id: str) -> str:
    """Return a validated Healthchecks ping URL without logging its UUID."""
    if state not in {"start", "success", "fail"}:
        raise ValueError(f"unsupported Healthchecks state: {state}")
    parts = urllib.parse.urlsplit(base_url.strip())
    path_parts = [item for item in parts.path.split("/") if item]
    if (
        parts.scheme != "https"
        or parts.hostname != "hc-ping.com"
        or parts.username is not None
        or parts.password is not None
        or len(path_parts) != 1
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "collector Healthchecks URL must be a base "
            "https://hc-ping.com/CHECK_UUID URL"
        )
    suffix = "" if state == "success" else f"/{state}"
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/") + suffix,
         urllib.parse.urlencode({"rid": run_id}), ""))


def ping_healthcheck(
    state: str,
    base_url: str | None = None,
    *,
    run_id: str | None = None,
    opener=urllib.request.urlopen,
    sleeper=time.sleep,
) -> None:
    """Best-effort dead-man ping; monitoring cannot break the guard."""
    configured = (
        os.environ.get(HEALTHCHECK_ENV, "")
        if base_url is None else base_url
    ).strip()
    if not configured:
        return
    try:
        target = healthcheck_target(
            configured, state, run_id or str(uuid.uuid4()))
    except ValueError as exc:
        print(f"healthcheck: {exc}", file=sys.stderr)
        return
    for attempt in range(3):
        try:
            request = urllib.request.Request(target, method="GET")
            with opener(request, timeout=10) as response:
                response.read(256)
            return
        except (OSError, urllib.error.URLError) as exc:
            if attempt == 2:
                print(
                    f"healthcheck: {state} ping failed: {exc}",
                    file=sys.stderr,
                )
            else:
                sleeper(0.5 * (attempt + 1))


def staleness_check(
    db_path: Path = LIVE_DB,
    marker: Path = STALE_MARKER,
    *,
    notifier=slack,
    health_ping=None,
) -> int:
    if health_ping is None:
        run_id = str(uuid.uuid4())

        def health_ping(state: str) -> None:
            ping_healthcheck(state, run_id=run_id)

    health_ping("start")
    try:
        age = last_success_age_minutes(db_path)
        problem = (
            "collector: no successful SIRI-VM poll found" if age is None
            else f"collector: last good SIRI-VM poll {age:.0f} min ago"
            if age > 10 else None
        )
        if problem:
            if not marker.exists():
                notifier(":rotating_light: bbb collector stale: " + problem)
                marker.touch()
            result = 1
        else:
            if marker.exists():
                marker.unlink()
                notifier(":white_check_mark: bbb collector is polling again")
            result = 0
    except Exception:
        health_ping("fail")
        raise
    health_ping("fail" if result else "success")
    return result


def main() -> int:
    return staleness_check()


if __name__ == "__main__":
    raise SystemExit(main())

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

from check_collector_freshness import (
    healthcheck_target,
    last_success_age_minutes,
    ping_healthcheck,
    staleness_check,
)
from compare_collectors import main as compatibility_main
from check_collector_freshness import main


def test_previous_entry_point_forwards_to_current_checker():
    assert compatibility_main is main


def test_staleness_reads_shared_collector_poller_status(tmp_path):
    db = tmp_path / "live.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE poller_status (name TEXT PRIMARY KEY, last_success_at TEXT)"
    )
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO poller_status VALUES (?, ?)",
        ("siri_vm", (now - timedelta(minutes=4)).isoformat()),
    )
    connection.commit()
    connection.close()

    assert last_success_age_minutes(db, now) == 4


def test_staleness_returns_none_for_uninitialised_database(tmp_path):
    db = tmp_path / "live.db"
    sqlite3.connect(db).close()
    assert last_success_age_minutes(db) is None


def _poller_db(path: Path, last_success: datetime) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE poller_status (name TEXT PRIMARY KEY, last_success_at TEXT)"
    )
    connection.execute(
        "INSERT INTO poller_status VALUES (?, ?)",
        ("siri_vm", last_success.isoformat()),
    )
    connection.commit()
    connection.close()


def test_healthcheck_targets_use_start_success_and_fail_paths():
    base = "https://hc-ping.com/example_UUID-123"
    start = urlsplit(healthcheck_target(base, "start", "run-1"))
    success = urlsplit(healthcheck_target(base, "success", "run-1"))
    failure = urlsplit(healthcheck_target(base, "fail", "run-1"))

    assert start.path.endswith("/example_UUID-123/start")
    assert success.path.endswith("/example_UUID-123")
    assert failure.path.endswith("/example_UUID-123/fail")
    assert parse_qs(start.query) == {"rid": ["run-1"]}


def test_staleness_check_pings_success_for_fresh_collector(tmp_path):
    now = datetime.now(timezone.utc)
    db = tmp_path / "live.db"
    marker = tmp_path / "stale"
    _poller_db(db, now)
    states = []

    assert staleness_check(
        db, marker, notifier=lambda _text: None, health_ping=states.append
    ) == 0
    assert states == ["start", "success"]
    assert not marker.exists()


def test_staleness_check_pings_failure_and_alerts_once(tmp_path):
    db = tmp_path / "live.db"
    marker = tmp_path / "stale"
    _poller_db(db, datetime.now(timezone.utc) - timedelta(minutes=20))
    states = []
    alerts = []

    assert staleness_check(
        db, marker, notifier=alerts.append, health_ping=states.append
    ) == 1
    assert states == ["start", "fail"]
    assert len(alerts) == 1
    assert marker.exists()


def test_healthcheck_network_failure_is_best_effort():
    calls = []

    def broken(_request, timeout):
        calls.append(timeout)
        raise OSError("offline")

    ping_healthcheck(
        "success",
        "https://hc-ping.com/example_UUID-123",
        opener=broken,
        sleeper=lambda _seconds: None,
    )
    assert calls == [10, 10, 10]


def test_healthcheck_can_pair_pings_with_one_run_id():
    targets = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size):
            return b""

    def capture(request, timeout):
        assert timeout == 10
        targets.append(request.full_url)
        return Response()

    for state in ("start", "success"):
        ping_healthcheck(
            state,
            "https://hc-ping.com/example_UUID-123",
            run_id="same-run",
            opener=capture,
        )

    assert [parse_qs(urlsplit(url).query)["rid"] for url in targets] == [
        ["same-run"],
        ["same-run"],
    ]

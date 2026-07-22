from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_integration as integration  # noqa: E402
import audit_promote  # noqa: E402


def database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE daily_overall_summary (
               service_date TEXT NOT NULL, operator TEXT NOT NULL
           )"""
    )
    conn.execute(
        """CREATE TABLE timepoint_observations (
               service_date TEXT NOT NULL,
               operator TEXT NOT NULL,
               route TEXT,
               trip_id TEXT NOT NULL,
               stop_sequence INTEGER NOT NULL,
               stop_code TEXT,
               observed_delay_s INTEGER,
               gps_distance_m INTEGER,
               vehicle_ref TEXT
           )"""
    )
    return conn


def add_completed(conn: sqlite3.Connection, dates: list[str]) -> None:
    conn.executemany(
        "INSERT INTO daily_overall_summary VALUES (?, 'ALL')",
        ((value,) for value in dates),
    )


def add_observation(conn: sqlite3.Connection, service_date: str, vehicle: str,
                    route: str, number: int, *, operator: str = "FBRI",
                    delay: int = 0, stop: str | None = None) -> None:
    conn.execute(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?)",
        (service_date, operator, route, f"{service_date}-{vehicle}-{route}-{number}",
         number, stop or f"STOP-{number}", delay, 25, vehicle),
    )


def dates_ending(value: date, count: int) -> list[str]:
    return [
        (value - timedelta(days=count - 1 - offset)).strftime("%Y%m%d")
        for offset in range(count)
    ]


def test_headline_is_count_weighted_and_profile_gate_is_enforced():
    conn = database()
    completed = ["20260714", "20260715", "20260716"]
    add_completed(conn, completed)
    for day in completed:
        for number in range(15):
            add_observation(conn, day, "FBRI-100", "75", number,
                            delay=0 if number < 10 else 600)
    # Plenty of readings but only one completed day: never publish a profile.
    for number in range(40):
        add_observation(conn, "20260716", "FBRI-ONE-DAY", "76", number)
    conn.commit()

    payload = integration.build_payload(
        conn, "20260716",
        now=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )

    assert payload["headline"] == {
        "measurement_start": "20260714",
        "through_date": "20260716",
        "readings": 85,
        "on_time": 70,
        "early": 0,
        "late": 15,
        "on_time_pct": 82.4,
        "minimum_readings": 30,
        "eligible": True,
    }
    assert [profile["vehicle_ref"] for profile in payload["profiles"]] == [
        "FBRI-100"
    ]
    profile = payload["profiles"][0]
    assert profile["observed_days"] == 3
    assert profile["readings"] == 45
    assert profile["slug"] == integration._slug("FBRI", "FBRI-100")
    assert profile["routes"] == [
        {
            "route": "75", "observed_days": 3, "readings": 45,
            "on_time": 30, "early": 0, "late": 15,
            "on_time_pct": 66.7,
            "days": [
                {"service_date": "20260716", "readings": 15,
                 "on_time": 10, "early": 0, "late": 5,
                 "on_time_pct": 66.7},
                {"service_date": "20260715", "readings": 15,
                 "on_time": 10, "early": 0, "late": 5,
                 "on_time_pct": 66.7},
                {"service_date": "20260714", "readings": 15,
                 "on_time": 10, "early": 0, "late": 5,
                 "on_time_pct": 66.7},
            ],
        }
    ]


def test_rare_detector_fails_closed_without_56_prior_completed_days():
    conn = database()
    completed = dates_ending(date(2026, 7, 17), 56)
    add_completed(conn, completed)
    conn.commit()

    payload = integration.build_payload(conn, completed[-1])

    assert payload["rare_workings"]["status"] == "insufficient_baseline"
    assert payload["rare_workings"]["baseline_days"] == 55
    assert payload["rare_workings"]["events"] == []


def test_rare_detector_retains_evidence_and_applies_cooldown():
    conn = database()
    completed = dates_ending(date(2026, 7, 17), 57)
    add_completed(conn, completed)
    prior, candidate = completed[:-1], completed[-1]

    # Vehicle V1 is established on another route for 20 service days.
    for day_index, day in enumerate(prior[:20]):
        for point in range(2):
            add_observation(conn, day, "FBRI-V1", "75", day_index * 10 + point)
    # Route X is established for this operator, but normally uses other buses.
    for day_index, day in enumerate(prior[:10]):
        add_observation(conn, day, f"FBRI-OTHER-{day_index}", "X", 1000 + day_index)
    # The target pair occurred only once, well outside the most recent 14 days.
    add_observation(conn, prior[0], "FBRI-V1", "X", 2000)
    # Three separate timing points corroborate today's rare allocation.
    for point in range(3):
        add_observation(conn, candidate, "FBRI-V1", "X", 3000 + point,
                        stop=f"X-{point}")
    conn.commit()

    payload = integration.build_payload(
        conn, candidate,
        now=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    rare = payload["rare_workings"]
    assert rare["status"] == "ready"
    assert rare["baseline_days"] == 56
    assert len(rare["events"]) == 1
    event = rare["events"][0]
    assert event["route"] == "X"
    assert event["evidence"]["vehicle_days"] == 20
    assert event["evidence"]["route_days"] == 10
    assert event["evidence"]["pair_days"] == 1
    assert event["evidence"]["recent_pair_days"] == 0
    assert event["evidence"]["candidate_points"] == 3
    stored = conn.execute(
        "SELECT queued, evidence_json FROM rare_working_evidence"
    ).fetchone()
    assert stored[0] == 1
    assert json.loads(stored[1])["cooldown_passed"] is True

    # A rerun is idempotent: it exposes the same one materialised event and
    # does not create another evidence row.
    second = integration.build_payload(conn, candidate)
    assert [item["event_id"] for item in second["rare_workings"]["events"]] == [
        event["event_id"]
    ]
    assert conn.execute("SELECT COUNT(*) FROM rare_working_evidence").fetchone()[0] == 1


def test_promote_sets_publish_time_only_after_input_is_valid(tmp_path):
    pending = tmp_path / "pending.json"
    published = tmp_path / "published.json"
    pending.write_text(json.dumps({"schema": 1, "published_at": None}),
                       encoding="utf-8")

    assert audit_promote.main([
        "--input", str(pending), "--output", str(published)
    ]) == 0
    result = json.loads(published.read_text(encoding="utf-8"))
    assert datetime.fromisoformat(result["published_at"]).tzinfo is not None

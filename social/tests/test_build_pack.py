from __future__ import annotations

import sys
import sqlite3
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest


SOCIAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOCIAL))

import build_pack  # noqa: E402


def audit_payload(*, gap: bool = False, readings: int = 200):
    days = []
    for index in range(7):
        day = 20 + index + (1 if gap and index == 6 else 0)
        on_time = 120 + index
        overall = {
            "readings_in_gate": readings,
            "on_time": on_time,
            "early": 20,
            "late": readings - on_time - 20,
            "on_time_pct": round(100 * on_time / readings, 1),
        }
        fleet = [
            {
                "model": "Electric model", "electric": True,
                "fuel": "electric", "readings_in_gate": 80,
                "on_time": 50, "on_time_pct": 62.5,
            },
            {
                "model": "Diesel model", "electric": False,
                "fuel": "diesel", "readings_in_gate": 100,
                "on_time": 70, "on_time_pct": 70.0,
            },
        ]
        days.append({
            "service_date": f"202607{day:02d}",
            "by_operator": {
                "ALL": {"overall": dict(overall), "fleet": fleet},
                "FBRI": {"overall": dict(overall), "fleet": fleet},
                "SCGL": {
                    "overall": {
                        "readings_in_gate": 80,
                        "on_time": 52,
                        "on_time_pct": 65.0,
                    },
                    "fleet": fleet,
                },
            },
        })
    return {
        "operator": "FBRI",
        "operator_name": "First Bristol",
        "operators": [
            {"code": "ALL", "name": "WECA network"},
            {"code": "FBRI", "name": "First Bristol"},
            {"code": "SCGL", "name": "Stagecoach West"},
        ],
        "target_pct": 95,
        "target_year": 2030,
        "current_target_pct": 82,
        "days": days,
    }


def recent_payload():
    return {"posts": [{
        "postText": "Exact final Bluesky text.",
        "postUrl": "https://bsky.app/profile/bristolbusbot.live/post/abc",
        "line": "75", "eventTimestamp": "2026-07-26T12:30:00Z",
        "operatorRef": "FBRI", "vehicleRef": "FBRI-100",
        "journeyRef": "J-1", "stopCode": "0100BRP", "stopName": "Bedminster Parade",
        "delaySeconds": 330,
    }]}


def test_pack_uses_exact_counts_and_successful_post_provenance():
    pack = build_pack.build_pack(
        audit_payload(), recent_payload(),
        now=datetime(2026, 7, 28, tzinfo=timezone.utc))
    assert pack["busWeek"]["readings"] == 1400
    assert pack["busWeek"]["onTimePct"] == 61.5
    assert pack["busWeek"]["operatorCode"] == "FBRI"
    assert pack["busWeek"]["operatorName"] == "First Bristol"
    assert pack["busWeek"]["operatorComparison"] == [
        {
            "operatorCode": "FBRI", "operatorName": "First Bristol",
            "readings": 1400, "onTime": 861, "onTimePct": 61.5,
        },
        {
            "operatorCode": "SCGL", "operatorName": "Stagecoach West",
            "readings": 560, "onTime": 364, "onTimePct": 65.0,
        },
    ]
    assert pack["busWeek"]["targetPct"] == 82
    assert pack["busWeek"]["targetGapPoints"] == 20.5
    assert pack["busWeek"]["longTermTargetPct"] == 95
    assert pack["busWeek"]["longTermTargetGapPoints"] == 33.5
    assert pack["busWeek"]["powertrain"]["identifiedReadings"] == 1260
    assert pack["busWeek"]["powertrain"]["unidentifiedReadings"] == 140
    assert pack["busWeek"]["powertrain"]["electric"]["sharePct"] == 44.4
    assert pack["busWeek"]["powertrain"]["electric"]["onTimePct"] == 62.5
    assert pack["busWeek"]["powertrain"]["dieselOther"]["onTimePct"] == 70.0
    assert pack["busWeek"]["serviceDays"] == 7
    assert pack["botSaid"]["postText"] == "Exact final Bluesky text."
    assert pack["botSaid"]["operatorName"] == "First Bristol"
    assert pack["botSaid"]["stop"] == "Bedminster Parade"
    assert pack["botSaid"]["delayMinutes"] == 6
    assert pack["botSaid"]["recentDepartures"] == [
        {"delaySeconds": 330, "isCurrent": True}]


def test_pack_uses_real_recent_stop_observations_for_receipt_strip():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE timepoint_observations (
               stop_code TEXT, observed_delay_s INTEGER,
               gps_distance_m INTEGER, recorded_at TEXT,
               siri_journey_ref TEXT, vehicle_ref TEXT
           )""")
    rows = []
    for index in range(20):
        rows.append((
            "0100BRP", index * 30, 25,
            f"2026-07-26T12:{index:02d}:00Z",
            "J-1" if index == 11 else f"J-{index}",
            "FBRI-100" if index == 11 else f"FBRI-{index}",
        ))
    conn.executemany(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?)", rows)
    bot = build_pack.build_bot_said(recent_payload(), conn)
    observations = bot["recentDepartures"]
    assert len(observations) == 20
    assert sum(bool(item.get("isCurrent")) for item in observations) == 1
    assert next(item for item in observations if item.get("isCurrent"))[
        "delaySeconds"] == 330


def test_weekly_distribution_uses_raw_rows_and_matches_rollup_counts():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE timepoint_observations (
               service_date TEXT, operator TEXT,
               observed_delay_s INTEGER, gps_distance_m INTEGER
           )""")
    payload = audit_payload()
    rows = []
    for index, day in enumerate(payload["days"]):
        on_time = 120 + index
        rows.extend((day["service_date"], "FBRI", 120, 25)
                    for _ in range(on_time))
        rows.extend((day["service_date"], "FBRI", 600, 25)
                    for _ in range(200 - on_time))
    conn.executemany(
        "INSERT INTO timepoint_observations VALUES (?,?,?,?)", rows)
    week = build_pack.build_week(payload)
    distribution = build_pack.build_distribution(conn, payload, week)
    assert sum(distribution["counts"]) == week["readings"]
    assert distribution["medianDelaySeconds"] == 120
    assert distribution["p90DelaySeconds"] == 600


def test_week_can_explicitly_build_the_whole_network():
    week = build_pack.build_week(audit_payload(), "ALL")
    assert week["operatorCode"] == "ALL"
    assert week["operatorName"] == "WECA network"


def test_week_gate_rejects_gaps_and_small_samples():
    try:
        build_pack.build_week(audit_payload(gap=True))
    except ValueError as exc:
        assert "not consecutive" in str(exc)
    else:
        raise AssertionError("date gap must fail")
    try:
        build_pack.build_week(audit_payload(readings=100))
    except ValueError as exc:
        assert "1,000" in str(exc)
    else:
        raise AssertionError("small sample must fail")


def make_app_db(path: Path, *, uri: str =
                "at://did:plc:bot/app.bsky.feed.post/abc",
                low_confidence: int = 0) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE engagement_analytics (
               id INTEGER PRIMARY KEY, operator_ref TEXT, vehicle_ref TEXT,
               line TEXT, journey_ref TEXT, event_timestamp TEXT,
               delay_seconds INTEGER, stop_code TEXT, stop_name TEXT,
               post_uri TEXT, post_content TEXT, low_confidence INTEGER
           )""")
    conn.execute(
        "INSERT INTO engagement_analytics VALUES (1,?,?,?,?,?,?,?,?,?,?,?)",
        ("FBRI", "FBRI-100", "75", "J-1",
         "2026-07-26T12:30:00Z", 330, "0100BRP",
         "Bedminster Parade", uri, "Exact final Bluesky text.",
         low_confidence))
    conn.commit()
    conn.close()


def test_single_card_reads_one_exact_full_uri_from_bot_db(tmp_path):
    app_db = tmp_path / "app_data.db"
    make_app_db(app_db)
    uri = "at://did:plc:bot/app.bsky.feed.post/abc"
    card = build_pack.read_bot_post(
        app_db, uri,
        "https://bsky.app/profile/bristolbusbot.live/post/abc")
    assert card["postUri"] == uri
    assert card["postText"] == "Exact final Bluesky text."
    assert card["operatorName"] == "First Bristol"
    assert card["journeyRef"] == "J-1"


def test_single_card_refuses_rkey_only_or_low_confidence(tmp_path):
    app_db = tmp_path / "app_data.db"
    make_app_db(app_db, low_confidence=1)
    with pytest.raises(ValueError, match="full Bluesky post AT URI"):
        build_pack.read_bot_post(
            app_db, "abc", "https://bsky.app/profile/x/post/abc")
    with pytest.raises(ValueError, match="low-confidence"):
        build_pack.read_bot_post(
            app_db, "at://did:plc:bot/app.bsky.feed.post/abc",
            "https://bsky.app/profile/bristolbusbot.live/post/abc")


def test_single_card_cli_writes_bot_only_pack(tmp_path):
    app_db = tmp_path / "app_data.db"
    output = tmp_path / "pack.json"
    make_app_db(app_db)
    assert build_pack.main([
        "--app-db", str(app_db),
        "--post-uri", "at://did:plc:bot/app.bsky.feed.post/abc",
        "--post-url", "https://bsky.app/profile/bristolbusbot.live/post/abc",
        "--output", str(output),
    ]) == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert set(payload) == {"generatedAt", "botSaid"}
    assert payload["botSaid"]["postUri"].endswith("/abc")

"""Site test fixtures.

The live.db fixture is built by the COLLECTOR's own code (same repo), so if
the collector's schema changes, these tests break loudly instead of the
site drifting out of sync silently. Install for tests:
    pip install -e ../collector -e ".[dev]"
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SITE))
# collector importable even without pip install (monorepo sibling)
sys.path.insert(0, str(SITE.parent / "collector" / "src"))

from collector import live_db  # noqa: E402  (the real schema owner)

from app import create_app  # noqa: E402
from app.config import Config  # noqa: E402

NOW = datetime(2026, 7, 1, 21, 0, 0, tzinfo=timezone.utc)


def _vehicle(conn, ref="FBRI-36205", line="75", delay_s=120, event="punctual",
             stop_code="0100B", stop_seq=2, trip="T_OUT", dest="Cribbs Causeway",
             lat=51.4550, lon=-2.5890, updated=None, recorded=None):
    conn.execute(
        """INSERT INTO vehicles (vehicle_ref, operator_ref, line, direction,
               destination, journey_ref, trip_id, match_tier,
               origin_aimed_departure, recorded_at, lat, lon, bearing,
               block_ref, delay_seconds, low_confidence, event_type,
               stop_code, stop_sequence, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ref, "FBRI", line, "outbound", dest, "2100", trip, "fuzzy",
         "2026-07-01T21:00:00+00:00", (recorded or NOW).isoformat(), lat, lon,
         185.0, "B1", delay_s, 0, event, stop_code, stop_seq,
         (updated or NOW).isoformat()))


@pytest.fixture
def app(tmp_path):
    # live.db via the collector's own schema
    live_path = tmp_path / "live.db"
    conn = live_db.connect(str(live_path))
    _vehicle(conn)                                             # punctual, mid-route
    _vehicle(conn, ref="FBRI-30052", delay_s=-120, stop_seq=1,
             stop_code="0100A", trip="T_OUT2")                 # waiting at origin
    _vehicle(conn, ref="FBRI-OLD", updated=NOW - timedelta(seconds=600))  # stale
    _vehicle(conn, ref="FBRI-DEPOT", delay_s=None, trip=None,
             lat=51.4205, lon=-2.5868, stop_code=None, stop_seq=None)     # Hengrove depot
    conn.execute("INSERT INTO poller_status VALUES ('siri_vm', ?, ?, 0, 'ok')",
                 (NOW.isoformat(), NOW.isoformat()))
    conn.commit(); conn.close()

    # tiny GTFS: just the stops table the site name-lookup needs
    gtfs_path = tmp_path / "timetable.db"
    g = sqlite3.connect(str(gtfs_path))
    g.execute("CREATE TABLE stops (stop_id TEXT, stop_code TEXT, stop_name TEXT,"
              " stop_lat REAL, stop_lon REAL)")
    g.executemany("INSERT INTO stops VALUES (?,?,?,?,?)", [
        ("S1", "0100A", "Origin Stop", 51.4600, -2.5890),
        ("S2", "0100B", "Middle Stop", 51.4550, -2.5890),
        ("S3", "0100C", "Hengrove Leisure Pk", 51.4500, -2.5890),
    ])
    g.execute("CREATE TABLE stop_times (trip_id TEXT, arrival_time TEXT,"
              " departure_time TEXT, stop_id TEXT, stop_sequence INT,"
              " timepoint INT)")
    # T_OUT: origin 22:00 local, middle 22:05, target 22:10 (vehicle is at
    # seq 2, so the S3 call at seq 3 is still ahead of it)
    g.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?,?)", [
        ("T_OUT", "22:00:00", "22:00:00", "S1", 1, 1),
        ("T_OUT", "22:05:00", "22:05:00", "S2", 2, 0),
        ("T_OUT", "22:10:00", "22:10:00", "S3", 3, 1),
        ("T_OUT2", "22:00:00", "22:00:00", "S1", 1, 1),
        ("T_OUT2", "22:12:00", "22:12:00", "S3", 2, 1),
    ])
    g.execute("CREATE TABLE routes (route_id TEXT, agency_id TEXT,"
              " route_short_name TEXT)")
    g.execute("CREATE TABLE stop_routes (stop_code TEXT NOT NULL,"
              " route_short_name TEXT NOT NULL,"
              " PRIMARY KEY (stop_code, route_short_name)) WITHOUT ROWID")
    g.execute("CREATE TABLE agency (agency_id TEXT, agency_noc TEXT)")
    g.execute("CREATE TABLE trips (trip_id TEXT, route_id TEXT, service_id TEXT,"
              " trip_headsign TEXT, direction_id INT,"
              " vehicle_journey_code TEXT)")
    g.execute("CREATE TABLE calendar (service_id TEXT, monday INT, tuesday INT,"
              " wednesday INT, thursday INT, friday INT, saturday INT,"
              " sunday INT, start_date TEXT, end_date TEXT)")
    g.execute("CREATE TABLE calendar_dates (service_id TEXT, date TEXT,"
              " exception_type INT)")
    g.execute("INSERT INTO agency VALUES ('OP1','FBRI')")
    g.execute("INSERT INTO routes VALUES ('R75','OP1','75')")
    g.executemany("INSERT INTO stop_routes VALUES (?, '75')", [
        ("0100A",), ("0100B",), ("0100C",),
    ])
    g.executemany("INSERT INTO trips VALUES (?,?,?,?,?,?)", [
        ("T_OUT", "R75", "WK", "Cribbs Causeway", 0, "VJ_2100"),
        ("T_OUT2", "R75", "WK", "Cribbs Causeway", 0, None),
        ("T_EXC", "R75", "EXC", "Extra Day Trip", 0, None),
        ("T_NIGHT", "R75", "WK", "Night Trip", 0, None),
    ])
    # WK: every day of July 2026; EXC: only via calendar_dates addition
    g.execute("INSERT INTO calendar VALUES ('WK',1,1,1,1,1,1,1,'20260701','20260731')")
    g.execute("INSERT INTO calendar VALUES ('EXC',0,0,0,0,0,0,0,'20260701','20260731')")
    g.execute("INSERT INTO calendar_dates VALUES ('EXC','20260701',1)")
    g.execute("INSERT INTO stop_times VALUES ('T_EXC','22:20:00','22:20:00','S3',1,1)")
    g.executemany("INSERT INTO stop_times VALUES (?,?,?,?,?,?)", [
        ("T_NIGHT", "24:10:00", "24:10:00", "S1", 1, 1),
        ("T_NIGHT", "24:20:00", "24:20:00", "S3", 2, 1),
    ])
    g.commit(); g.close()

    fleet_path = tmp_path / "fleet.json"
    fleet_path.write_text(json.dumps({
        "36205": {"reg": "YX23ABC", "livery": {"name": "First Bristol",
                                               "left": "#e63946"},
                  "vehicle_type": {"name": "Yutong E12", "electric": True,
                                   "double_decker": False, "fuel": "electric"},
                  "garage": {"name": "Hengrove"}},
    }))

    for name, blurb in (("desc", "a fine electric bus"),
                        ("waiting", "limbering up to depart"),
                        ("depot", "fast asleep at the shed")):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps({"36205": blurb, "30052": blurb, "DEPOT": blurb}))
    cfg = Config(live_db=str(live_path), timetable_db=str(gtfs_path),
                 fleet_json=str(fleet_path),
                 descriptions_json=str(tmp_path / "desc.json"),
                 waiting_json=str(tmp_path / "waiting.json"),
                 depot_descriptions_json=str(tmp_path / "depot.json"))
    application = create_app(cfg)
    application.testing = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()

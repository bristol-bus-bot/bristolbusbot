from datetime import datetime, timezone

from collector.delay import LiveEstimate
from collector.live_db import (CORROBORATION_EXTREME, connect, decide_event,
                               prune_consumed_events, record_poll,
                               upsert_vehicle)
from collector.matching import Match
from collector.siri import VehicleSnapshot


def snap(ref="FBRI-36205", journey="1115",
         origin="2026-06-10T11:15:00+01:00",
         recorded=datetime(2026, 6, 10, 10, 20, tzinfo=timezone.utc)):
    return VehicleSnapshot(
        vehicle_ref=ref, operator_ref="FBRI", line="75", direction="outbound",
        lat=51.45, lon=-2.58, recorded_utc=recorded,
        journey_ref=journey, origin_aimed_departure=origin,
        destination_raw="Hengrove", bearing=185.0, block_ref="7012",
        origin_stop_ref=None, destination_stop_ref=None)


def est(delay_s, event_type):
    return LiveEstimate(delay_s=delay_s, stop_code="0100B", stop_name="Middle",
                        stop_sequence=2,
                        distance_m=40, low_confidence=False, event_type=event_type,
                        scheduled_local=datetime(2026, 6, 10, 11, 20))


MATCH = Match(trip_id="T_OUT", route_short_name="75", schedule=[], tier="fuzzy")


def events(conn):
    return conn.execute("SELECT * FROM events ORDER BY id").fetchall()


def test_single_poll_never_emits():
    conn = connect()
    d = upsert_vehicle(conn, snap(), est(400, "delayed"), MATCH)
    assert not d.emit and d.corroboration == 1
    assert events(conn) == []


def test_two_agreeing_polls_emit_once():
    conn = connect()
    upsert_vehicle(conn, snap(), est(400, "delayed"), MATCH)
    d2 = upsert_vehicle(conn, snap(), est(430, "delayed"), MATCH)  # within ±120s
    assert d2.emit and d2.corroboration == 2
    d3 = upsert_vehicle(conn, snap(), est(450, "delayed"), MATCH)
    assert not d3.emit and d3.reason == "already-emitted"
    assert len(events(conn)) == 1


def test_disagreeing_polls_reset_streak():
    conn = connect()
    upsert_vehicle(conn, snap(), est(400, "delayed"), MATCH)
    d2 = upsert_vehicle(conn, snap(), est(900, "delayed"), MATCH)  # jumped 500s
    assert not d2.emit and d2.corroboration == 1
    assert events(conn) == []


def test_worsening_reemits():
    conn = connect()
    upsert_vehicle(conn, snap(), est(400, "delayed"), MATCH)
    upsert_vehicle(conn, snap(), est(420, "delayed"), MATCH)      # emit @ ~420
    upsert_vehicle(conn, snap(), est(500, "delayed"), MATCH)      # not enough worse
    upsert_vehicle(conn, snap(), est(560, "delayed"), MATCH)
    d = upsert_vehicle(conn, snap(), est(640, "delayed"), MATCH)  # small climbs...
    upsert_vehicle(conn, snap(), est(730, "delayed"), MATCH)      # >= 420+300 -> re-emit
    evs = events(conn)
    assert len(evs) == 2 and evs[1]["delay_seconds"] == 730


def test_extreme_needs_three_polls():
    conn = connect()
    upsert_vehicle(conn, snap(), est(2000, "delayed"), MATCH)   # 33 min late
    d2 = upsert_vehicle(conn, snap(), est(2050, "delayed"), MATCH)
    assert not d2.emit and d2.corroboration == 2
    d3 = upsert_vehicle(conn, snap(), est(2100, "delayed"), MATCH)
    assert d3.emit and d3.corroboration == CORROBORATION_EXTREME
    assert events(conn)[0]["delay_seconds"] == 2100


def test_new_journey_resets_dedupe():
    conn = connect()
    upsert_vehicle(conn, snap(journey="1115"), est(400, "delayed"), MATCH)
    upsert_vehicle(conn, snap(journey="1115"), est(420, "delayed"), MATCH)  # emits
    # Same vehicle, NEW journey later in the day: dedupe must not suppress it
    upsert_vehicle(conn, snap(journey="1415"), est(400, "delayed"), MATCH)
    d = upsert_vehicle(conn, snap(journey="1415"), est(410, "delayed"), MATCH)
    assert d.emit
    assert len(events(conn)) == 2


def test_same_journey_ref_on_next_day_is_a_new_run():
    conn = connect()
    first = snap(journey="1115", origin="2026-06-10T11:15:00+01:00")
    next_day = snap(
        journey="1115", origin="2026-06-11T11:15:00+01:00",
        recorded=datetime(2026, 6, 11, 10, 20, tzinfo=timezone.utc))
    upsert_vehicle(conn, first, est(400, "delayed"), MATCH)
    upsert_vehicle(conn, first, est(420, "delayed"), MATCH)  # emits day one
    d1 = upsert_vehicle(conn, next_day, est(400, "delayed"), MATCH)
    d2 = upsert_vehicle(conn, next_day, est(410, "delayed"), MATCH)
    assert not d1.emit and d2.emit
    assert len(events(conn)) == 2


def test_punctual_clears_streak_keeps_vehicle():
    conn = connect()
    upsert_vehicle(conn, snap(), est(400, "delayed"), MATCH)
    upsert_vehicle(conn, snap(), est(30, "punctual"), MATCH)
    d3 = upsert_vehicle(conn, snap(), est(400, "delayed"), MATCH)
    assert d3.corroboration == 1 and not d3.emit
    row = conn.execute("SELECT * FROM vehicles").fetchone()
    assert row["line"] == "75" and row["block_ref"] == "7012"


def test_no_estimate_keeps_position_visible():
    conn = connect()
    d = upsert_vehicle(conn, snap(), None, None)
    assert not d.emit
    row = conn.execute("SELECT * FROM vehicles").fetchone()
    assert row["delay_seconds"] is None and row["lat"] == 51.45


def test_record_poll_tracks_failures():
    conn = connect()
    record_poll(conn, "siri_vm", ok=True)
    record_poll(conn, "siri_vm", ok=False)
    record_poll(conn, "siri_vm", ok=False)
    row = conn.execute("SELECT * FROM poller_status WHERE name='siri_vm'").fetchone()
    assert row["consecutive_failures"] == 2 and row["last_success_at"] is not None


def test_schema_version_mismatch_rebuilds(tmp_path):
    import sqlite3
    from collector.live_db import SCHEMA_VERSION, connect
    path = str(tmp_path / "live.db")
    # simulate an old-schema db: version 1 with a vehicles table lacking columns
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE vehicles (vehicle_ref TEXT PRIMARY KEY)")
    old.execute("PRAGMA user_version = 1")
    old.commit(); old.close()
    conn = connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(vehicles)")]
    assert "stop_sequence" in cols
    conn.close()

    # and the pre-versioning case: user_version 0 but old tables present
    import os
    os.remove(path)
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE vehicles (vehicle_ref TEXT PRIMARY KEY)")
    old.commit(); old.close()
    conn = connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(vehicles)")]
    assert "stop_sequence" in cols and "distance_m" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def _insert_event(conn, created_at, consumed_at):
    conn.execute(
        """INSERT INTO events (created_at, vehicle_ref, operator_ref, line,
               direction, journey_ref, origin_aimed_departure, stop_code,
               stop_name, delay_seconds, event_type, source, corroboration,
               lat, lon, block_ref, low_confidence, consumed_by_bot_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (created_at, "FBRI-1", "FBRI", "75", "outbound", "1115", None,
         "0100B", "Middle", 400, "delayed", "live_estimate", 2,
         51.45, -2.58, None, 0, consumed_at))


def test_prune_removes_only_old_consumed_events():
    from datetime import timedelta
    conn = connect()
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=10)).isoformat()
    fresh = (now - timedelta(hours=1)).isoformat()
    _insert_event(conn, old, consumed_at=old)      # old + consumed: pruned
    _insert_event(conn, old, consumed_at=None)     # old, never consumed: kept
    _insert_event(conn, fresh, consumed_at=fresh)  # fresh + consumed: kept
    assert prune_consumed_events(conn) == 1
    rows = conn.execute(
        "SELECT created_at, consumed_by_bot_at FROM events ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0][1] is None          # the unconsumed old row survived
    assert rows[1][0] == fresh         # the fresh consumed row survived

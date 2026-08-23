#!/usr/bin/env python3
"""Snapshot scheduled trips for the audit's daily service denominator.

The snapshot applies GTFS calendar and calendar_dates rules for the selected
service date. It reads timetable.db and writes expected trips to audit.db.
"""

import os
import sys
import sqlite3
from datetime import datetime
from dateutil import tz

from audit_operators import SHOW_OPERATORS

HERE = os.path.dirname(os.path.abspath(__file__))
TIMETABLE_DB = os.getenv("BBB_TIMETABLE_DB", os.path.join(HERE, "timetable.db"))
AUDIT_DB = os.getenv("BBB_AUDIT_DB", os.path.join(HERE, "audit.db"))

TARGET_TZ_STR = "Europe/London"
TARGET_TZ = tz.gettz(TARGET_TZ_STR) or tz.tzlocal()
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
EXPECTED_TRIP_DETAIL_COLUMNS = {
    "route_id": "TEXT",
    "service_id": "TEXT",
    "block_id": "TEXT",
    "vehicle_journey_code": "TEXT",
    "first_stop_id": "TEXT",
    "first_stop_code": "TEXT",
    "timetable_edition": "TEXT",
    "last_departure": "TEXT",
}


def bounded_text(value, limit=256):
    """Keep imported identifiers useful without allowing pathological growth."""
    if value is None:
        return None
    return str(value)[:limit]


def table_exists(cur, table):
    return cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def hhmm_ref(departure_time):
    """First-stop GTFS departure 'HH:MM:SS' -> HHMM string, matching the form
    First Bus puts in SIRI DatedVehicleJourneyRef (e.g. '18:25:00' -> '1825').
    GTFS hours >=24 are wrapped to clock hours so it lines up with the live ref."""
    if not departure_time:
        return None
    try:
        p = departure_time.split(":")
        h, m = int(p[0]), int(p[1])
        return f"{h % 24:02d}{m:02d}"
    except (ValueError, TypeError, IndexError):
        return None


def init_expected_table(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute(
        """CREATE TABLE IF NOT EXISTS expected_trips (
               service_date    TEXT NOT NULL,
               operator        TEXT NOT NULL,
               route           TEXT,
               trip_id         TEXT NOT NULL,
               siri_ref        TEXT,
               direction       INTEGER,
               first_departure TEXT,
               route_id        TEXT,
               service_id      TEXT,
               block_id        TEXT,
               vehicle_journey_code TEXT,
               first_stop_id   TEXT,
               first_stop_code TEXT,
               timetable_edition TEXT,
               last_departure TEXT,
               PRIMARY KEY (service_date, trip_id)
           )"""
    )
    # CREATE TABLE IF NOT EXISTS does not upgrade the durable audit database.
    # Additive nullable columns preserve every existing row and let old history
    # remain explicitly "detail not captured" rather than being guessed later.
    existing = {row[1] for row in cur.execute("PRAGMA table_info(expected_trips)")}
    for name, declaration in EXPECTED_TRIP_DETAIL_COLUMNS.items():
        if name not in existing:
            cur.execute(
                f"ALTER TABLE expected_trips ADD COLUMN {name} {declaration}")
    cur.execute(
        """CREATE INDEX IF NOT EXISTS idx_expected_date_route
               ON expected_trips (service_date, operator, route)"""
    )
    conn.commit()


def active_service_ids(cur, date_str, weekday_col):
    """Return the set of service_ids active on date_str, applying
    calendar weekday/range rules plus calendar_dates additions/removals."""
    # (a) regular calendar services active on this weekday & in range
    cur.execute(
        f"""SELECT DISTINCT c.service_id
            FROM calendar c
            WHERE c.{weekday_col} = 1
              AND c.start_date <= ? AND c.end_date >= ?""",
        (date_str, date_str),
    )
    base = {r[0] for r in cur.fetchall()}

    # removals (exception_type = 2) for this date
    cur.execute(
        "SELECT service_id FROM calendar_dates WHERE date = ? AND exception_type = 2",
        (date_str,),
    )
    removed = {r[0] for r in cur.fetchall()}

    # additions (exception_type = 1) for this date
    cur.execute(
        "SELECT service_id FROM calendar_dates WHERE date = ? AND exception_type = 1",
        (date_str,),
    )
    added = {r[0] for r in cur.fetchall()}

    return (base - removed) | added


def build_snapshot(date_str):
    weekday_col = DAYS[datetime.strptime(date_str, "%Y%m%d").weekday()]

    tt_conn = sqlite3.connect(f"file:{TIMETABLE_DB}?mode=ro", uri=True)
    tt_cur = tt_conn.cursor()

    service_ids = active_service_ids(tt_cur, date_str, weekday_col)
    if not service_ids:
        print(f"No active service_ids for {date_str} ({weekday_col}). "
              f"Check timetable.db is current.")
        tt_conn.close()
        return 0

    # All show-operator trips on those services, with the minimum private
    # timetable clues needed to distinguish similar duties later. The route
    # edition uses the same route_service_editions vocabulary as the matching
    # evidence receipts; older timetable databases without that optional table
    # remain readable and yield a blank edition.
    svc_ph = ",".join("?" for _ in service_ids)
    op_ph = ",".join("?" for _ in SHOW_OPERATORS)
    has_editions = table_exists(tt_cur, "route_service_editions")
    edition_sql = "NULL"
    if has_editions:
        edition_sql = """(SELECT rse.edition_start
                              FROM route_service_editions rse
                             WHERE rse.route_id = t.route_id
                               AND rse.edition_start <= ?
                               AND rse.effective_end >= ?
                             ORDER BY rse.edition_start DESC LIMIT 1)"""
    sql = f"""
        SELECT t.trip_id, a.agency_noc, r.route_short_name, t.direction_id,
               (SELECT st.departure_time FROM stop_times st
                WHERE st.trip_id = t.trip_id
                ORDER BY st.stop_sequence LIMIT 1)
                   AS first_departure,
               t.route_id, t.service_id, t.block_id,
               t.vehicle_journey_code,
               (SELECT st.stop_id FROM stop_times st
                WHERE st.trip_id = t.trip_id
                ORDER BY st.stop_sequence LIMIT 1)
                   AS first_stop_id,
               (SELECT s.stop_code FROM stop_times st
                LEFT JOIN stops s ON s.stop_id = st.stop_id
                WHERE st.trip_id = t.trip_id
                ORDER BY st.stop_sequence LIMIT 1)
                   AS first_stop_code,
               {edition_sql} AS timetable_edition,
               (SELECT st.departure_time FROM stop_times st
                WHERE st.trip_id = t.trip_id
                ORDER BY st.stop_sequence DESC LIMIT 1)
                   AS last_departure
        FROM trips t
        JOIN routes r ON t.route_id = r.route_id
        JOIN agency a ON r.agency_id = a.agency_id
        WHERE a.agency_noc IN ({op_ph})
          AND t.service_id IN ({svc_ph})
    """
    edition_params = [date_str, date_str] if has_editions else []
    tt_cur.execute(
        sql, edition_params + list(SHOW_OPERATORS) + list(service_ids))
    rows = tt_cur.fetchall()
    tt_conn.close()

    audit_conn = sqlite3.connect(AUDIT_DB)
    init_expected_table(audit_conn)
    audit_cur = audit_conn.cursor()

    # Replace any existing snapshot for this date (idempotent re-runs).
    audit_cur.execute("DELETE FROM expected_trips WHERE service_date = ?", (date_str,))

    written = 0
    for row in rows:
        (trip_id, operator, route_short, direction_id, first_departure,
         route_id, service_id, block_id, vehicle_journey_code,
         first_stop_id, first_stop_code, timetable_edition,
         last_departure) = row
        audit_cur.execute(
            """INSERT OR REPLACE INTO expected_trips
                   (service_date, operator, route, trip_id, siri_ref,
                    direction, first_departure, route_id, service_id, block_id,
                    vehicle_journey_code, first_stop_id, first_stop_code,
                    timetable_edition, last_departure)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (date_str, operator, route_short, trip_id,
             hhmm_ref(first_departure), direction_id, first_departure,
             bounded_text(route_id), bounded_text(service_id),
             bounded_text(block_id), bounded_text(vehicle_journey_code),
             bounded_text(first_stop_id), bounded_text(first_stop_code),
             bounded_text(timetable_edition, 8),
             bounded_text(last_departure, 32)),
        )
        written += 1

    audit_conn.commit()
    audit_conn.close()
    return written


def main():
    if not os.path.exists(TIMETABLE_DB):
        print(f"ERROR: timetable.db not found at {TIMETABLE_DB}")
        return

    if len(sys.argv) > 1:
        date_str = sys.argv[1].strip()
        try:
            datetime.strptime(date_str, "%Y%m%d")
        except ValueError:
            print(f"ERROR: date must be YYYYMMDD, got '{date_str}'")
            return
    else:
        date_str = datetime.now(TARGET_TZ).strftime("%Y%m%d")

    weekday = DAYS[datetime.strptime(date_str, "%Y%m%d").weekday()]
    print(f"Building WECA scheduled-trips snapshot for {date_str} ({weekday})...")
    print(f"  operators: {', '.join(SHOW_OPERATORS)}")
    n = build_snapshot(date_str)
    print(f"  wrote {n} expected trips -> {AUDIT_DB} (table: expected_trips)")
    if n:
        conn = sqlite3.connect(AUDIT_DB)
        cur = conn.cursor()
        cur.execute(
            """SELECT operator, COUNT(*) c FROM expected_trips
               WHERE service_date = ? GROUP BY operator ORDER BY c DESC""",
            (date_str,),
        )
        print("  by operator:", ", ".join(f"{o}:{c}" for o, c in cur.fetchall()))
        conn.close()


if __name__ == "__main__":
    main()

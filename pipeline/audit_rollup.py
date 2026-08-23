#!/usr/bin/env python3
"""
Daily rollup and pruning for the Bristol bus audit.

Turns the raw timing-point observations and the scheduled-trips snapshot into
per-day, per-route summaries the public site serves, then prunes raw rows older
than the retention window so audit.db stays small. Reads and writes audit.db
only. Full method and rationale are in AUDIT_METHODOLOGY.md.

Run from the bristol-live-buses folder:
    python audit_rollup.py            roll up today
    python audit_rollup.py 20260601   roll up a specific YYYYMMDD
    python audit_rollup.py 20260601 --no-prune
    python audit_rollup.py --backfill-geo-routes
"""

import os
import sys
import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from dateutil import tz

from audit_operators import SHOW_OPERATORS, NETWORK_LABEL
from audit_geo import load_geo_index, geo_for
from audit_fleet import load_fleet_index, fleet_for, fleet_number

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_DB = os.getenv("BBB_AUDIT_DB", os.path.join(HERE, "audit.db"))
SQLITE_BUSY_TIMEOUT_MS = 60_000

TARGET_TZ = tz.gettz("Europe/London") or tz.tzlocal()

DISTANCE_GATE_M = 150
ON_TIME_LOW_S = -60
ON_TIME_HIGH_S = 359
RAW_RETENTION_DAYS = 95
MIN_GEO_MATCH_PCT = 90.0

# A coverage day is evidence only when the collector was present for the whole
# scheduled operating window.  Production polls every 30 seconds; the margins
# allow a bus to appear shortly before its first departure and up to an hour
# after the final trip starts.
COLLECTOR_POLL_INTERVAL_S = 30
POLL_WINDOW_BEFORE_FIRST_S = 15 * 60
POLL_WINDOW_AFTER_LAST_S = 60 * 60
MIN_SUCCESSFUL_POLL_COVERAGE_PCT = 90.0
MIN_SUCCESSFUL_POLL_RATE_PCT = 95.0
MAX_POLL_BOUNDARY_GAP_S = 5 * 60
MAX_SUCCESSFUL_POLL_GAP_S = 15 * 60
MIN_MATCH_RATE_PCT = 80.0
UNKNOWN_ROUTE = "(unknown)"
UNKNOWN_DIRECTION = -1

DELAY_BUCKETS = [
    "early_5plus",
    "early_1_5",
    "on_time",
    "late_6_10",
    "late_10_20",
    "late_20plus",
]

PEAK_BANDS = ["am_peak", "interpeak", "pm_peak", "evening"]


def connect_audit_db(path=None):
    """Wait out one short collector write instead of failing the rollup."""
    conn = sqlite3.connect(
        path or AUDIT_DB,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
    )
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    return conn


def migrate_overall_pk(cur):
    """Older databases had daily_overall_summary keyed on service_date alone,
    which cannot hold one row per operator. Rebuild it with a composite
    (service_date, operator) key, preserving existing rows."""
    row = cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='daily_overall_summary'"
    ).fetchone()
    if not row or not row[0] or "PRIMARY KEY (service_date, operator)" in row[0]:
        return
    cur.execute("ALTER TABLE daily_overall_summary RENAME TO daily_overall_summary_old")
    cur.execute(
        """CREATE TABLE daily_overall_summary (
               service_date        TEXT NOT NULL,
               operator            TEXT NOT NULL,
               readings_in_gate    INTEGER,
               on_time             INTEGER,
               early               INTEGER,
               late                INTEGER,
               on_time_pct         REAL,
               mean_delay_s        INTEGER,
               median_delay_s      INTEGER,
               readings_total      INTEGER,
               excluded_distance   INTEGER,
               median_gate_dist_m  INTEGER,
               expected_trips      INTEGER,
               observed_trips      INTEGER,
               coverage_pct        REAL,
               PRIMARY KEY (service_date, operator)
           )"""
    )
    cur.execute("INSERT INTO daily_overall_summary SELECT * FROM daily_overall_summary_old")
    cur.execute("DROP TABLE daily_overall_summary_old")
    print("  migrated daily_overall_summary to (service_date, operator) key.")


def init_summary_tables(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    raw_tables = {row[0] for row in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "timepoint_observations" in raw_tables:
        raw_columns = {row[1] for row in cur.execute(
            "PRAGMA table_info(timepoint_observations)")}
        if "is_origin" not in raw_columns:
            cur.execute(
                "ALTER TABLE timepoint_observations ADD COLUMN "
                "is_origin INTEGER NOT NULL DEFAULT 0")
        if "match_tier" not in raw_columns:
            cur.execute(
                "ALTER TABLE timepoint_observations ADD COLUMN match_tier TEXT")
    if "expected_trips" in raw_tables:
        expected_columns = {row[1] for row in cur.execute(
            "PRAGMA table_info(expected_trips)")}
        if "direction" not in expected_columns:
            cur.execute("ALTER TABLE expected_trips ADD COLUMN direction INTEGER")
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_route_summary (
               service_date        TEXT NOT NULL,
               operator            TEXT NOT NULL,
               route               TEXT,
               readings_in_gate    INTEGER,
               on_time             INTEGER,
               early               INTEGER,
               late                INTEGER,
               on_time_pct         REAL,
               mean_delay_s        INTEGER,
               median_delay_s      INTEGER,
               readings_total      INTEGER,
               excluded_distance   INTEGER,
               median_gate_dist_m  INTEGER,
               expected_trips      INTEGER,
               observed_trips      INTEGER,
               coverage_pct        REAL,
               PRIMARY KEY (service_date, operator, route)
           )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_overall_summary (
               service_date        TEXT NOT NULL,
               operator            TEXT NOT NULL,
               readings_in_gate    INTEGER,
               on_time             INTEGER,
               early               INTEGER,
               late                INTEGER,
               on_time_pct         REAL,
               mean_delay_s        INTEGER,
               median_delay_s      INTEGER,
               readings_total      INTEGER,
               excluded_distance   INTEGER,
               median_gate_dist_m  INTEGER,
               expected_trips      INTEGER,
               observed_trips      INTEGER,
               coverage_pct        REAL,
               PRIMARY KEY (service_date, operator)
           )"""
    )
    migrate_overall_pk(cur)
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_delay_histogram (
               service_date  TEXT NOT NULL,
               operator      TEXT NOT NULL,
               route         TEXT,
               bucket        TEXT NOT NULL,
               n             INTEGER NOT NULL
           )"""
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_hist_date ON daily_delay_histogram(service_date, operator)"
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_peak_summary (
               service_date      TEXT NOT NULL,
               operator          TEXT NOT NULL,
               route             TEXT,
               peak_band         TEXT NOT NULL,
               readings_in_gate  INTEGER,
               on_time           INTEGER,
               early             INTEGER,
               late              INTEGER,
               on_time_pct       REAL,
               mean_delay_s      INTEGER,
               median_delay_s    INTEGER
           )"""
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS ix_peak_date ON daily_peak_summary(service_date, operator)"
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_geo_summary (
               service_date      TEXT NOT NULL,
               operator          TEXT NOT NULL,
               geo_type          TEXT NOT NULL,
               geo_key           TEXT NOT NULL,
               readings_in_gate  INTEGER,
               on_time           INTEGER,
               on_time_pct       REAL,
               mean_delay_s      INTEGER,
               median_delay_s    INTEGER,
               PRIMARY KEY (service_date, operator, geo_type, geo_key)
           )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_geo_route_summary (
               service_date      TEXT NOT NULL,
               operator          TEXT NOT NULL,
               source_operator   TEXT NOT NULL,
               geo_type          TEXT NOT NULL,
               geo_key           TEXT NOT NULL,
               route             TEXT NOT NULL,
               readings_in_gate  INTEGER,
               on_time           INTEGER,
               early             INTEGER,
               late              INTEGER,
               on_time_pct       REAL,
               mean_delay_s      INTEGER,
               median_delay_s    INTEGER,
               PRIMARY KEY (
                   service_date, operator, source_operator,
                   geo_type, geo_key, route
               )
           )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_fleet_summary (
               service_date      TEXT NOT NULL,
               operator          TEXT NOT NULL,
               model             TEXT NOT NULL,
               electric          INTEGER,
               fuel              TEXT,
               vehicles          INTEGER,
               readings_in_gate  INTEGER,
               on_time           INTEGER,
               on_time_pct       REAL,
               mean_delay_s      INTEGER,
               median_delay_s    INTEGER,
               routes_json       TEXT,
               PRIMARY KEY (service_date, operator, model)
           )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_route_class (
               service_date  TEXT NOT NULL,
               operator      TEXT NOT NULL,
               route         TEXT NOT NULL,
               frequent      INTEGER,
               peak_hourly   INTEGER,
               PRIMARY KEY (service_date, operator, route)
           )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_trip_coverage_days (
               service_date                 TEXT PRIMARY KEY,
               is_valid                     INTEGER NOT NULL,
               invalid_reasons_json         TEXT NOT NULL,
               poll_window_start            TEXT,
               poll_window_end              TEXT,
               expected_polls               INTEGER NOT NULL,
               recorded_polls               INTEGER NOT NULL,
               successful_polls             INTEGER NOT NULL,
               successful_poll_rate_pct     REAL,
               successful_poll_coverage_pct REAL,
               max_successful_poll_gap_s     INTEGER,
               candidate_readings           INTEGER NOT NULL,
               matched_readings             INTEGER NOT NULL,
               match_rate_pct               REAL,
               scheduled_trips              INTEGER NOT NULL,
               observed_trips               INTEGER NOT NULL,
               unobserved_trips             INTEGER NOT NULL,
               exact_observed_trips         INTEGER NOT NULL,
               fuzzy_observed_trips         INTEGER NOT NULL,
               unknown_tier_observed_trips  INTEGER NOT NULL,
               invalid_departure_times      INTEGER NOT NULL
           )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS daily_trip_coverage (
               service_date                TEXT NOT NULL,
               operator                    TEXT NOT NULL,
               route                       TEXT NOT NULL,
               direction                   INTEGER NOT NULL,
               time_band                   TEXT NOT NULL,
               scheduled_trips             INTEGER NOT NULL,
               observed_trips              INTEGER NOT NULL,
               unobserved_trips            INTEGER NOT NULL,
               exact_observed_trips        INTEGER NOT NULL,
               fuzzy_observed_trips        INTEGER NOT NULL,
               unknown_tier_observed_trips INTEGER NOT NULL,
               PRIMARY KEY (
                   service_date, operator, route, direction, time_band
               ),
               FOREIGN KEY (service_date)
                   REFERENCES daily_trip_coverage_days(service_date)
           )"""
    )
    cur.execute(
        """CREATE VIEW IF NOT EXISTS valid_daily_trip_coverage AS
               SELECT coverage.*
               FROM daily_trip_coverage coverage
               JOIN daily_trip_coverage_days day
                 ON day.service_date = coverage.service_date
               WHERE day.is_valid = 1"""
    )
    conn.commit()


def delay_band(delay_s):
    if delay_s < ON_TIME_LOW_S:
        return "early"
    if delay_s > ON_TIME_HIGH_S:
        return "late"
    return "on_time"


def delay_bucket(delay_s):
    if delay_s < -300:
        return "early_5plus"
    if delay_s < ON_TIME_LOW_S:
        return "early_1_5"
    if delay_s <= ON_TIME_HIGH_S:
        return "on_time"
    if delay_s <= 600:
        return "late_6_10"
    if delay_s <= 1200:
        return "late_10_20"
    return "late_20plus"


def peak_band_for(scheduled_local):
    try:
        hour = int(scheduled_local[11:13])
    except (TypeError, ValueError, IndexError):
        return "evening"
    if 7 <= hour <= 9:
        return "am_peak"
    if 10 <= hour <= 15:
        return "interpeak"
    if 16 <= hour <= 18:
        return "pm_peak"
    return "evening"


def new_accumulator():
    return {
        "delays": [],
        "gate_dists": [],
        "on_time": 0,
        "early": 0,
        "late": 0,
        "readings_total": 0,
        "excluded_distance": 0,
        "hist": {bucket: 0 for bucket in DELAY_BUCKETS},
        "peak": {
            band: {"delays": [], "on_time": 0, "early": 0, "late": 0}
            for band in PEAK_BANDS
        },
    }


def fold_into(target, source):
    target["delays"].extend(source["delays"])
    target["gate_dists"].extend(source["gate_dists"])
    target["on_time"] += source["on_time"]
    target["early"] += source["early"]
    target["late"] += source["late"]
    target["readings_total"] += source["readings_total"]
    target["excluded_distance"] += source["excluded_distance"]
    for bucket in DELAY_BUCKETS:
        target["hist"][bucket] += source["hist"][bucket]
    for band in PEAK_BANDS:
        source_band = source["peak"][band]
        target_band = target["peak"][band]
        target_band["delays"].extend(source_band["delays"])
        target_band["on_time"] += source_band["on_time"]
        target_band["early"] += source_band["early"]
        target_band["late"] += source_band["late"]


def punctuality_stats(accumulator):
    delays = accumulator["delays"]
    in_gate = len(delays)
    return {
        "in_gate": in_gate,
        "on_time_pct": round(100.0 * accumulator["on_time"] / in_gate, 1) if in_gate else None,
        "mean_delay": int(round(statistics.mean(delays))) if delays else None,
        "median_delay": int(round(statistics.median(delays))) if delays else None,
        "median_dist": int(round(statistics.median(accumulator["gate_dists"]))) if accumulator["gate_dists"] else None,
    }


def peak_band_row(band_stats):
    delays = band_stats["delays"]
    in_gate = len(delays)
    return (
        in_gate,
        band_stats["on_time"],
        band_stats["early"],
        band_stats["late"],
        round(100.0 * band_stats["on_time"] / in_gate, 1) if in_gate else None,
        int(round(statistics.mean(delays))) if delays else None,
        int(round(statistics.median(delays))) if delays else None,
    )


def gtfs_time_seconds(value):
    """Parse an extended GTFS time such as 29:42:00 without wrapping it."""
    if not value:
        return None
    try:
        hour, minute, second = (int(part) for part in value.split(":")[:3])
    except (TypeError, ValueError):
        return None
    if hour < 0 or not 0 <= minute < 60 or not 0 <= second < 60:
        return None
    return hour * 3600 + minute * 60 + second


def trip_time_band_for(first_departure):
    seconds = gtfs_time_seconds(first_departure)
    if seconds is None:
        return "unknown"
    hour = (seconds // 3600) % 24
    if 7 <= hour <= 9:
        return "am_peak"
    if 10 <= hour <= 15:
        return "interpeak"
    if 16 <= hour <= 18:
        return "pm_peak"
    return "evening"


def load_trip_coverage_rows(conn, date_str, operators):
    """Return the scheduled trips and whether the existing audit saw them.

    This is the one trip-coverage definition used by both the public route
    summary and the new private, more detailed rollup.  An observation that is
    not in the day's scheduled snapshot cannot inflate coverage.
    """
    op_ph = ",".join("?" for _ in operators)
    rows = conn.execute(
        f"""WITH observed AS (
                SELECT trip_id,
                       MAX(CASE WHEN match_tier = 'exact' THEN 1 ELSE 0 END)
                           AS had_exact,
                       MAX(CASE WHEN match_tier = 'fuzzy' THEN 1 ELSE 0 END)
                           AS had_fuzzy
                FROM timepoint_observations
                WHERE service_date = ? AND operator IN ({op_ph})
                GROUP BY trip_id
            )
            SELECT expected.operator, expected.route, expected.trip_id,
                   COALESCE(expected.direction, ?), expected.first_departure,
                   CASE WHEN observed.trip_id IS NULL THEN 0 ELSE 1 END,
                   COALESCE(observed.had_exact, 0),
                   COALESCE(observed.had_fuzzy, 0)
            FROM expected_trips expected
            LEFT JOIN observed ON observed.trip_id = expected.trip_id
            WHERE expected.service_date = ?
              AND expected.operator IN ({op_ph})""",
        (date_str, *operators, UNKNOWN_DIRECTION, date_str, *operators),
    ).fetchall()
    return [
        {
            "operator": operator,
            "route": route or UNKNOWN_ROUTE,
            "trip_id": trip_id,
            "direction": direction,
            "first_departure": first_departure,
            "observed": bool(observed),
            "match_tier": (
                "exact" if had_exact else "fuzzy" if had_fuzzy
                else "unknown" if observed else None
            ),
        }
        for (operator, route, trip_id, direction, first_departure,
             observed, had_exact, had_fuzzy) in rows
    ]


def route_trip_counts(conn, date_str, operators):
    expected = {}
    observed = {}
    for row in load_trip_coverage_rows(conn, date_str, operators):
        route = None if row["route"] == UNKNOWN_ROUTE else row["route"]
        expected[route] = expected.get(route, 0) + 1
        if row["observed"]:
            observed[route] = observed.get(route, 0) + 1
    return expected, observed


def scheduled_poll_window(date_str, rows):
    departure_seconds = [
        gtfs_time_seconds(row["first_departure"]) for row in rows
    ]
    valid_seconds = [value for value in departure_seconds if value is not None]
    invalid_count = len(departure_seconds) - len(valid_seconds)
    if not valid_seconds:
        return None, None, invalid_count
    midnight = datetime.strptime(date_str, "%Y%m%d").replace(tzinfo=TARGET_TZ)
    start = midnight + timedelta(
        seconds=min(valid_seconds) - POLL_WINDOW_BEFORE_FIRST_S)
    end = midnight + timedelta(
        seconds=max(valid_seconds) + POLL_WINDOW_AFTER_LAST_S)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), invalid_count


def collector_quality(conn, window_start, window_end):
    empty = {
        "poll_window_start": None,
        "poll_window_end": None,
        "expected_polls": 0,
        "recorded_polls": 0,
        "successful_polls": 0,
        "successful_poll_rate_pct": None,
        "successful_poll_coverage_pct": None,
        "max_successful_poll_gap_s": None,
        "candidate_readings": 0,
        "matched_readings": 0,
        "match_rate_pct": None,
        "reasons": [],
    }
    if window_start is None or window_end is None:
        return empty

    poll_rows = conn.execute(
        """SELECT poll_at, ok, candidates, matched
           FROM poll_log
           WHERE poll_at >= ? AND poll_at < ?
           ORDER BY poll_at""",
        (window_start.isoformat(), window_end.isoformat()),
    ).fetchall()
    expected_polls = max(
        1, round((window_end - window_start).total_seconds()
                 / COLLECTOR_POLL_INTERVAL_S))
    successful_times = []
    for poll_at, ok, _candidates, _matched in poll_rows:
        if ok:
            try:
                parsed = datetime.fromisoformat(poll_at.replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                continue
            successful_times.append(parsed.astimezone(timezone.utc))

    recorded = len(poll_rows)
    successful = sum(bool(row[1]) for row in poll_rows)
    success_rate = (
        round(100.0 * successful / recorded, 2) if recorded else None)
    completeness = round(
        min(100.0, 100.0 * successful / expected_polls), 2)
    candidates = sum(int(row[2] or 0) for row in poll_rows)
    matched = sum(int(row[3] or 0) for row in poll_rows)
    match_rate = (
        round(100.0 * matched / candidates, 2) if candidates else None)

    reasons = []
    max_gap = None
    if successful_times:
        boundary_start = (successful_times[0] - window_start).total_seconds()
        boundary_end = (window_end - successful_times[-1]).total_seconds()
        internal_gaps = [
            (right - left).total_seconds()
            for left, right in zip(successful_times, successful_times[1:])
        ]
        max_gap = round(max([boundary_start, boundary_end, *internal_gaps]))
        if boundary_start > MAX_POLL_BOUNDARY_GAP_S:
            reasons.append("collector_started_after_schedule_window")
        if boundary_end > MAX_POLL_BOUNDARY_GAP_S:
            reasons.append("collector_stopped_before_schedule_window_closed")
        if internal_gaps and max(internal_gaps) > MAX_SUCCESSFUL_POLL_GAP_S:
            reasons.append("successful_poll_gap_over_15_minutes")
    else:
        reasons.append("no_successful_polls")

    if completeness < MIN_SUCCESSFUL_POLL_COVERAGE_PCT:
        reasons.append("successful_poll_coverage_below_90pct")
    if success_rate is None or success_rate < MIN_SUCCESSFUL_POLL_RATE_PCT:
        reasons.append("successful_poll_rate_below_95pct")
    if not candidates:
        reasons.append("no_in_area_match_candidates")
    elif match_rate < MIN_MATCH_RATE_PCT:
        reasons.append("matching_rate_below_80pct")

    return {
        "poll_window_start": window_start.isoformat(),
        "poll_window_end": window_end.isoformat(),
        "expected_polls": expected_polls,
        "recorded_polls": recorded,
        "successful_polls": successful,
        "successful_poll_rate_pct": success_rate,
        "successful_poll_coverage_pct": completeness,
        "max_successful_poll_gap_s": max_gap,
        "candidate_readings": candidates,
        "matched_readings": matched,
        "match_rate_pct": match_rate,
        "reasons": reasons,
    }


def rollup_trip_coverage(conn, date_str, operators=SHOW_OPERATORS):
    rows = load_trip_coverage_rows(conn, date_str, operators)
    window_start, window_end, invalid_times = scheduled_poll_window(
        date_str, rows)
    quality = collector_quality(conn, window_start, window_end)
    reasons = list(quality.pop("reasons"))
    if not rows:
        reasons.append("no_scheduled_trips")
    if invalid_times:
        reasons.append("invalid_departure_times")
    if rows and window_start is None:
        reasons.append("no_valid_departure_times")
    reasons = sorted(set(reasons))

    groups = {}
    totals = {
        "scheduled": len(rows), "observed": 0, "exact": 0,
        "fuzzy": 0, "unknown": 0,
    }
    for row in rows:
        band = trip_time_band_for(row["first_departure"])
        for label in (row["operator"], NETWORK_LABEL):
            key = (label, row["route"], row["direction"], band)
            group = groups.setdefault(key, {
                "scheduled": 0, "observed": 0, "exact": 0,
                "fuzzy": 0, "unknown": 0,
            })
            group["scheduled"] += 1
            if row["observed"]:
                group["observed"] += 1
                group[row["match_tier"]] += 1
        if row["observed"]:
            totals["observed"] += 1
            totals[row["match_tier"]] += 1

    day_values = (
        date_str, int(not reasons), json.dumps(reasons, separators=(",", ":")),
        quality["poll_window_start"], quality["poll_window_end"],
        quality["expected_polls"], quality["recorded_polls"],
        quality["successful_polls"], quality["successful_poll_rate_pct"],
        quality["successful_poll_coverage_pct"],
        quality["max_successful_poll_gap_s"], quality["candidate_readings"],
        quality["matched_readings"], quality["match_rate_pct"],
        totals["scheduled"], totals["observed"],
        totals["scheduled"] - totals["observed"], totals["exact"],
        totals["fuzzy"], totals["unknown"], invalid_times,
    )

    with conn:
        conn.execute(
            "DELETE FROM daily_trip_coverage WHERE service_date = ?",
            (date_str,),
        )
        conn.execute(
            """INSERT OR REPLACE INTO daily_trip_coverage_days VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            day_values,
        )
        for (operator, route, direction, band), group in sorted(groups.items()):
            conn.execute(
                """INSERT INTO daily_trip_coverage VALUES
                       (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    date_str, operator, route, direction, band,
                    group["scheduled"], group["observed"],
                    group["scheduled"] - group["observed"], group["exact"],
                    group["fuzzy"], group["unknown"],
                ),
            )
    return {
        "valid": not reasons,
        "invalid_reasons": reasons,
        **quality,
        **totals,
        "unobserved": totals["scheduled"] - totals["observed"],
        "groups": len(groups),
    }


def trip_coverage_backfill_dates(conn):
    cutoff = (
        datetime.now(TARGET_TZ) - timedelta(days=RAW_RETENTION_DAYS)
    ).strftime("%Y%m%d")
    latest_complete = (
        datetime.now(TARGET_TZ) - timedelta(days=1)
    ).strftime("%Y%m%d")
    return [
        row[0] for row in conn.execute(
            """SELECT DISTINCT service_date FROM expected_trips
               WHERE service_date BETWEEN ? AND ? ORDER BY service_date""",
            (cutoff, latest_complete),
        )
    ]


def print_trip_coverage_report(result):
    status = "valid" if result["valid"] else (
        "INVALID: " + ", ".join(result["invalid_reasons"]))
    print(
        f"  private trip coverage: {result['observed']} of "
        f"{result['scheduled']} scheduled trips observed; "
        f"{result['unobserved']} not observed; {status}."
    )
    print(
        f"    collector: {result['successful_polls']}/"
        f"{result['recorded_polls']} successful polls; "
        f"{result['successful_poll_coverage_pct']}% of expected poll slots; "
        f"match rate {result['match_rate_pct']}%."
    )


def rollup(conn, date_str, operators, label, coverage_valid=True):
    cur = conn.cursor()
    op_ph = ",".join("?" for _ in operators)

    cur.execute(
        f"""SELECT route, observed_delay_s, gps_distance_m, scheduled_local
           FROM timepoint_observations
           WHERE service_date = ? AND operator IN ({op_ph})
             AND COALESCE(is_origin, 0) = 0""",
        (date_str, *operators),
    )
    observations = cur.fetchall()

    if not observations:
        existing = cur.execute(
            "SELECT 1 FROM daily_overall_summary WHERE service_date = ? AND operator = ?",
            (date_str, label),
        ).fetchone()
        return {"skipped": True, "had_summary": bool(existing)}

    per_route = {}
    for route, delay_s, dist_m, scheduled_local in observations:
        stats = per_route.setdefault(route, new_accumulator())
        stats["readings_total"] += 1
        if dist_m is None or dist_m > DISTANCE_GATE_M:
            stats["excluded_distance"] += 1
            continue
        stats["delays"].append(delay_s)
        stats["gate_dists"].append(dist_m)
        band = delay_band(delay_s)
        stats[band] += 1
        stats["hist"][delay_bucket(delay_s)] += 1
        band_stats = stats["peak"][peak_band_for(scheduled_local)]
        band_stats["delays"].append(delay_s)
        band_stats[band] += 1

    expected_by_route, observed_by_route = route_trip_counts(
        conn, date_str, operators)

    all_routes = set(per_route) | set(expected_by_route) | set(observed_by_route)
    network_totals = new_accumulator()

    cur.execute(
        "DELETE FROM daily_route_summary WHERE service_date = ? AND operator = ?",
        (date_str, label),
    )
    cur.execute(
        "DELETE FROM daily_delay_histogram WHERE service_date = ? AND operator = ?",
        (date_str, label),
    )
    cur.execute(
        "DELETE FROM daily_peak_summary WHERE service_date = ? AND operator = ?",
        (date_str, label),
    )

    def write_histogram(route, accumulator):
        for bucket in DELAY_BUCKETS:
            count = accumulator["hist"][bucket]
            if count:
                cur.execute(
                    "INSERT INTO daily_delay_histogram VALUES (?,?,?,?,?)",
                    (date_str, label, route, bucket, count),
                )

    def write_peak(route, accumulator):
        for band in PEAK_BANDS:
            band_stats = accumulator["peak"][band]
            if not band_stats["delays"]:
                continue
            cur.execute(
                "INSERT INTO daily_peak_summary VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (date_str, label, route, band) + peak_band_row(band_stats),
            )

    for route in sorted(all_routes, key=lambda value: (value is None, value)):
        stats = per_route.get(route, new_accumulator())
        summary = punctuality_stats(stats)
        expected_count = expected_by_route.get(route, 0)
        observed_count = observed_by_route.get(route, 0)
        coverage = (
            round(100.0 * observed_count / expected_count, 1)
            if coverage_valid and expected_count else None)
        expected = expected_count if coverage_valid else None
        observed = observed_count if coverage_valid else None

        cur.execute(
            "INSERT INTO daily_route_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                date_str, label, route,
                summary["in_gate"], stats["on_time"], stats["early"], stats["late"], summary["on_time_pct"],
                summary["mean_delay"], summary["median_delay"],
                stats["readings_total"], stats["excluded_distance"], summary["median_dist"],
                expected, observed, coverage,
            ),
        )
        write_histogram(route, stats)
        write_peak(route, stats)
        fold_into(network_totals, stats)

    overall = punctuality_stats(network_totals)
    expected_count_total = sum(expected_by_route.values())
    observed_count_total = sum(observed_by_route.values())
    coverage_total = (
        round(100.0 * observed_count_total / expected_count_total, 1)
        if coverage_valid and expected_count_total else None)
    expected_total = expected_count_total if coverage_valid else None
    observed_total = observed_count_total if coverage_valid else None

    cur.execute(
        "INSERT OR REPLACE INTO daily_overall_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            date_str, label,
            overall["in_gate"], network_totals["on_time"], network_totals["early"], network_totals["late"], overall["on_time_pct"],
            overall["mean_delay"], overall["median_delay"],
            network_totals["readings_total"], network_totals["excluded_distance"], overall["median_dist"],
            expected_total, observed_total, coverage_total,
        ),
    )
    write_histogram(None, network_totals)
    write_peak(None, network_totals)
    conn.commit()

    return {
        "in_gate": overall["in_gate"],
        "on_time_pct": overall["on_time_pct"],
        "mean_delay": overall["mean_delay"],
        "readings_total": network_totals["readings_total"],
        "excluded_distance": network_totals["excluded_distance"],
        "median_dist": overall["median_dist"],
        "expected": expected_total,
        "observed": observed_total,
        "coverage_pct": coverage_total,
        "coverage_valid": coverage_valid,
        "hist": network_totals["hist"],
        "peak": {band: len(network_totals["peak"][band]["delays"]) for band in PEAK_BANDS},
    }


def prune_old_raw(conn, before_date_str):
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM timepoint_observations WHERE service_date < ?",
        (before_date_str,),
    )
    count = cur.fetchone()[0]
    poll_count = cur.execute(
        "SELECT COUNT(*) FROM poll_log WHERE substr(poll_at,1,10) < ?",
        (f"{before_date_str[:4]}-{before_date_str[4:6]}-{before_date_str[6:8]}",),
    ).fetchone()[0]
    if count or poll_count:
        cur.execute(
            "DELETE FROM timepoint_observations WHERE service_date < ?",
            (before_date_str,),
        )
        iso_cutoff = f"{before_date_str[:4]}-{before_date_str[4:6]}-{before_date_str[6:8]}"
        cur.execute(
            "DELETE FROM poll_log WHERE substr(poll_at,1,10) < ?",
            (iso_cutoff,),
        )
        conn.commit()
    return count


def resolve_date(args):
    if not args:
        return datetime.now(TARGET_TZ).strftime("%Y%m%d")
    date_str = args[0].strip()
    datetime.strptime(date_str, "%Y%m%d")
    return date_str


def print_report(result):
    if result.get("skipped"):
        if result["had_summary"]:
            print("  no observations for this date; existing summary left untouched (not overwritten).")
        else:
            print("  no observations for this date; nothing to roll up.")
        return
    if result["readings_total"] == 0:
        print("  no timing-point observations for this date (collector not running, or wrong date).")
    else:
        on_time = f"{result['on_time_pct']}%" if result["on_time_pct"] is not None else "n/a"
        mean = f"{result['mean_delay']}s" if result["mean_delay"] is not None else "n/a"
        print(f"  punctuality: {on_time} on-time  (mean {mean})")
        print(
            f"    readings: {result['in_gate']} counted / {result['readings_total']} total "
            f"({result['excluded_distance']} excluded >{DISTANCE_GATE_M}m; "
            f"median kept distance {result['median_dist']}m)"
        )
        print("    distribution: " + ", ".join(f"{bucket}={result['hist'][bucket]}" for bucket in DELAY_BUCKETS))
        print("    by slot (readings): " + ", ".join(f"{band}={result['peak'][band]}" for band in PEAK_BANDS))
    if not result.get("coverage_valid", True):
        print(
            "  coverage: withheld because the collector evidence for this "
            "service day is incomplete."
        )
        return
    coverage = f"{result['coverage_pct']}%" if result["coverage_pct"] is not None else "n/a"
    print(
        f"  coverage: {result['observed']} of {result['expected']} scheduled trips observed "
        f"({coverage})  [proxy, not proven cancellations]"
    )


def _geo_route_buckets(conn, date_str, operators, geo_index):
    """Build route-aware geography buckets from retained, in-gate readings."""
    cur = conn.cursor()
    op_ph = ",".join("?" for _ in operators)
    cur.execute(
        f"""SELECT operator, stop_code, route, observed_delay_s
            FROM timepoint_observations
            WHERE service_date = ? AND operator IN ({op_ph})
              AND COALESCE(is_origin, 0) = 0
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?""",
        (date_str, *operators, DISTANCE_GATE_M),
    )
    geography = {}
    routes = {}
    for source_operator, stop_code, route, delay_s in cur.fetchall():
        place = geo_for(geo_index, stop_code)
        if not place:
            continue
        for geo_type, geo_key in (
                ("area", place["area"]), ("ward", place["ward"])):
            acc = geography.setdefault(
                (geo_type, geo_key), {"delays": [], "on_time": 0})
            acc["delays"].append(delay_s)
            if ON_TIME_LOW_S <= delay_s <= ON_TIME_HIGH_S:
                acc["on_time"] += 1
            if route:
                route_acc = routes.setdefault(
                    (source_operator, geo_type, geo_key, route),
                    {"delays": [], "on_time": 0, "early": 0, "late": 0},
                )
                route_acc["delays"].append(delay_s)
                band = delay_band(delay_s)
                route_acc[band] += 1
    return geography, routes


def _write_geo_routes(conn, date_str, label, buckets):
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM daily_geo_route_summary "
        "WHERE service_date = ? AND operator = ?",
        (date_str, label),
    )
    for (source_operator, geo_type, geo_key, route), acc in buckets.items():
        delays = acc["delays"]
        count = len(delays)
        cur.execute(
            """INSERT INTO daily_geo_route_summary
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                date_str, label, source_operator, geo_type, geo_key, route, count,
                acc["on_time"], acc["early"], acc["late"],
                round(100.0 * acc["on_time"] / count, 1) if count else None,
                int(round(statistics.mean(delays))) if delays else None,
                int(round(statistics.median(delays))) if delays else None,
            ),
        )


def rollup_geo_routes(conn, date_str, operators, label, geo_index):
    """Backfillable route-by-area/ward evidence; public figures are untouched."""
    _, route_buckets = _geo_route_buckets(
        conn, date_str, operators, geo_index)
    _write_geo_routes(conn, date_str, label, route_buckets)
    conn.commit()
    return len(route_buckets)


def rollup_geo(conn, date_str, operators, label, geo_index):
    """Aggregate in-gate readings by WECA area and ward, for the given operator
    set, into daily_geo_summary. Additive; does not touch the route rollup."""
    cur = conn.cursor()
    buckets, route_buckets = _geo_route_buckets(
        conn, date_str, operators, geo_index)

    cur.execute(
        "DELETE FROM daily_geo_summary WHERE service_date = ? AND operator = ?",
        (date_str, label),
    )
    for (geo_type, geo_key), acc in buckets.items():
        delays = acc["delays"]
        n = len(delays)
        cur.execute(
            "INSERT INTO daily_geo_summary VALUES (?,?,?,?,?,?,?,?,?)",
            (date_str, label, geo_type, geo_key, n, acc["on_time"],
             round(100.0 * acc["on_time"] / n, 1) if n else None,
             int(round(statistics.mean(delays))) if delays else None,
             int(round(statistics.median(delays))) if delays else None),
        )
    _write_geo_routes(conn, date_str, label, route_buckets)
    conn.commit()
    return len(buckets)


def geography_match_stats(conn, date_str, operators, geo_index):
    """Measure lookup coverage before any summary rows are changed."""
    cur = conn.cursor()
    op_ph = ",".join("?" for _ in operators)
    cur.execute(
        f"""SELECT stop_code FROM timepoint_observations
            WHERE service_date = ? AND operator IN ({op_ph})
              AND COALESCE(is_origin, 0) = 0
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?""",
        (date_str, *operators, DISTANCE_GATE_M),
    )
    eligible = matched = 0
    for (stop_code,) in cur.fetchall():
        eligible += 1
        if geo_for(geo_index, stop_code):
            matched += 1
    pct = round(100.0 * matched / eligible, 1) if eligible else None
    return {"eligible": eligible, "matched": matched, "pct": pct}


def rollup_fleet(conn, date_str, operators, label, fleet_index):
    """Aggregate in-gate readings by vehicle model (with electric flag and the
    service numbers each model runs), for the given operator set, into
    daily_fleet_summary. Additive."""
    cur = conn.cursor()
    op_ph = ",".join("?" for _ in operators)
    cur.execute(
        f"""SELECT operator, route, vehicle_ref, observed_delay_s
            FROM timepoint_observations
            WHERE service_date = ? AND operator IN ({op_ph})
              AND COALESCE(is_origin, 0) = 0
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?""",
        (date_str, *operators, DISTANCE_GATE_M),
    )
    models = {}
    for op, route, vehicle_ref, delay_s in cur.fetchall():
        f = fleet_for(fleet_index, op, vehicle_ref)
        if not f:
            continue
        m = models.setdefault(f["model"], {
            "electric": f["electric"], "fuel": f["fuel"],
            "delays": [], "on_time": 0, "vehicles": set(), "routes": {},
        })
        m["delays"].append(delay_s)
        if ON_TIME_LOW_S <= delay_s <= ON_TIME_HIGH_S:
            m["on_time"] += 1
        fn = fleet_number(vehicle_ref)
        if fn:
            m["vehicles"].add(fn)
        if route:
            m["routes"][route] = m["routes"].get(route, 0) + 1

    cur.execute(
        "DELETE FROM daily_fleet_summary WHERE service_date = ? AND operator = ?",
        (date_str, label),
    )
    for model, m in models.items():
        delays = m["delays"]
        n = len(delays)
        top_routes = sorted(m["routes"].items(), key=lambda kv: -kv[1])[:8]
        cur.execute(
            """INSERT INTO daily_fleet_summary
                   (service_date, operator, model, electric, fuel, vehicles,
                    readings_in_gate, on_time, on_time_pct, mean_delay_s,
                    median_delay_s, routes_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (date_str, label, model, 1 if m["electric"] else 0, m["fuel"],
             len(m["vehicles"]), n, m["on_time"],
             round(100.0 * m["on_time"] / n, 1) if n else None,
             int(round(statistics.mean(delays))) if delays else None,
             int(round(statistics.median(delays))) if delays else None,
             json.dumps(top_routes)),
        )
    conn.commit()
    return len(models)


def rollup_frequency(conn, date_str, operators, label):
    """Classify each route frequent vs non-frequent from the scheduled trips.
    Frequent = 6+ departures in its busiest daytime hour (DfT's high-frequency
    threshold), which the official standard measures by excess wait time rather
    than timetable punctuality. Additive; writes daily_route_class."""
    cur = conn.cursor()
    op_ph = ",".join("?" for _ in operators)
    cur.execute(
        f"""SELECT route, first_departure FROM expected_trips
            WHERE service_date = ? AND operator IN ({op_ph})""",
        (date_str, *operators),
    )
    hourly = {}
    for route, first_departure in cur.fetchall():
        if not first_departure:
            continue
        try:
            hour = int(first_departure[:2]) % 24
        except (ValueError, TypeError):
            continue
        if 6 <= hour <= 19:
            hours = hourly.setdefault(route, {})
            hours[hour] = hours.get(hour, 0) + 1

    cur.execute(
        "DELETE FROM daily_route_class WHERE service_date = ? AND operator = ?",
        (date_str, label),
    )
    frequent_count = 0
    for route, hours in hourly.items():
        peak = max(hours.values()) if hours else 0
        frequent = 1 if peak >= 6 else 0
        frequent_count += frequent
        cur.execute(
            "INSERT INTO daily_route_class VALUES (?,?,?,?,?)",
            (date_str, label, route, frequent, peak),
        )
    conn.commit()
    return frequent_count


def main():
    if not os.path.exists(AUDIT_DB):
        print(f"ERROR: audit.db not found at {AUDIT_DB} (run the collector first).")
        return 1

    raw_args = sys.argv[1:]
    no_prune = "--no-prune" in raw_args
    backfill_trip_coverage = "--backfill-trip-coverage" in raw_args
    backfill_geo_routes = "--backfill-geo-routes" in raw_args
    positional = [arg for arg in raw_args if not arg.startswith("--")]

    if backfill_trip_coverage and backfill_geo_routes:
        print("ERROR: choose one backfill mode at a time.")
        return 2
    if (backfill_trip_coverage or backfill_geo_routes) and positional:
        print("ERROR: backfill modes do not accept a date.")
        return 2

    conn = connect_audit_db()
    init_summary_tables(conn)
    if backfill_trip_coverage:
        dates = trip_coverage_backfill_dates(conn)
        print(f"Backfilling private trip coverage for {len(dates)} retained days...")
        for date_str in dates:
            print(f"[{date_str}]")
            print_trip_coverage_report(
                rollup_trip_coverage(conn, date_str, SHOW_OPERATORS))
        conn.close()
        return 0
    if backfill_geo_routes:
        geo_index = load_geo_index()
        dates = [
            row[0] for row in conn.execute(
                """SELECT DISTINCT service_date
                     FROM timepoint_observations
                    ORDER BY service_date""")
        ]
        print(
            f"Backfilling private route-by-geography evidence for "
            f"{len(dates)} retained days...")
        for date_str in dates:
            print(f"[{date_str}]")
            for op in SHOW_OPERATORS:
                rollup_geo_routes(conn, date_str, [op], op, geo_index)
            groups = rollup_geo_routes(
                conn, date_str, SHOW_OPERATORS, NETWORK_LABEL, geo_index)
            print(f"  {groups} route/place groups")
        conn.close()
        return 0

    try:
        date_str = resolve_date(positional)
    except ValueError:
        conn.close()
        print(f"ERROR: date must be YYYYMMDD, got '{positional[0]}'")
        return 2

    coverage_quality = rollup_trip_coverage(
        conn, date_str, SHOW_OPERATORS)
    print_trip_coverage_report(coverage_quality)
    geo_index = load_geo_index()
    fleet_index = load_fleet_index()
    geo_match = geography_match_stats(
        conn, date_str, SHOW_OPERATORS, geo_index
    )
    if (geo_match["eligible"]
            and geo_match["pct"] < MIN_GEO_MATCH_PCT):
        raise RuntimeError(
            "audit geography matched only "
            f"{geo_match['matched']}/{geo_match['eligible']} readings "
            f"({geo_match['pct']}%; minimum {MIN_GEO_MATCH_PCT}%)"
        )

    print(f"Rolling up WECA operators for {date_str}...")
    for op in SHOW_OPERATORS:
        print(f"[{op}]")
        print_report(rollup(
            conn, date_str, [op], op,
            coverage_valid=coverage_quality["valid"]))
    print(f"[{NETWORK_LABEL}] whole network")
    print_report(rollup(
        conn, date_str, SHOW_OPERATORS, NETWORK_LABEL,
        coverage_valid=coverage_quality["valid"]))

    for op in SHOW_OPERATORS:
        rollup_geo(conn, date_str, [op], op, geo_index)
    n = rollup_geo(conn, date_str, SHOW_OPERATORS, NETWORK_LABEL, geo_index)
    match_text = (
        f"{geo_match['matched']}/{geo_match['eligible']} readings matched"
        if geo_match["eligible"] else "no eligible readings"
    )
    print(f"  geography: {n} area/ward groups rolled up; {match_text}.")

    for op in SHOW_OPERATORS:
        rollup_fleet(conn, date_str, [op], op, fleet_index)
    n = rollup_fleet(conn, date_str, SHOW_OPERATORS, NETWORK_LABEL, fleet_index)
    print(f"  fleet: {n} models rolled up.")

    for op in SHOW_OPERATORS:
        rollup_frequency(conn, date_str, [op], op)
    n = rollup_frequency(conn, date_str, SHOW_OPERATORS, NETWORK_LABEL)
    print(f"  frequency: {n} frequent routes classified.")

    if not no_prune:
        cutoff = (datetime.now(TARGET_TZ) - timedelta(days=RAW_RETENTION_DAYS)).strftime("%Y%m%d")
        pruned = prune_old_raw(conn, cutoff)
        if pruned:
            print(f"  pruned {pruned} raw observations older than {cutoff} (rollups kept).")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

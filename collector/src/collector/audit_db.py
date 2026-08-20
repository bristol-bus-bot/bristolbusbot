"""Write the audit database schema consumed by rollup and export jobs.

Schema changes must be coordinated with every downstream reader and the
published methodology.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

MATCHING_EVIDENCE_DAILY_LIMIT = 250
MATCHING_EVIDENCE_TOTAL_LIMIT = 5000
MATCHING_EVIDENCE_ALTERNATIVES_LIMIT = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS timepoint_observations (
    service_date     TEXT NOT NULL,
    operator         TEXT NOT NULL,
    route            TEXT,
    trip_id          TEXT NOT NULL,
    siri_journey_ref TEXT,
    stop_sequence    INTEGER NOT NULL,
    stop_code        TEXT,
    scheduled_local  TEXT,
    observed_delay_s INTEGER,
    on_time          INTEGER,
    gps_distance_m   INTEGER,
    recorded_at      TEXT,
    vehicle_ref      TEXT,
    is_origin        INTEGER NOT NULL DEFAULT 0,
    match_tier       TEXT,
    PRIMARY KEY (service_date, trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_obs_date_route
    ON timepoint_observations (service_date, operator, route);
CREATE TABLE IF NOT EXISTS poll_log (
    poll_at         TEXT PRIMARY KEY,
    ok              INTEGER,
    vehicles_total  INTEGER,
    candidates      INTEGER,
    matched         INTEGER,
    obs_written     INTEGER,
    dropped_insane  INTEGER,
    stale           INTEGER NOT NULL DEFAULT 0,
    evidence_written INTEGER NOT NULL DEFAULT 0,
    evidence_dropped INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS matching_evidence (
    evidence_id              TEXT PRIMARY KEY,
    captured_at              TEXT NOT NULL,
    service_date             TEXT NOT NULL,
    reasons_json             TEXT NOT NULL,
    calculation_reasons_json TEXT NOT NULL,
    operator                 TEXT,
    route                    TEXT,
    vehicle_ref              TEXT,
    direction                TEXT,
    journey_ref              TEXT,
    origin_aimed_departure   TEXT,
    recorded_at              TEXT,
    lat                      REAL,
    lon                      REAL,
    bearing                  REAL,
    block_ref                TEXT,
    chosen_trip_id           TEXT,
    match_tier               TEXT,
    candidate_count          INTEGER,
    candidates_truncated     INTEGER NOT NULL DEFAULT 0,
    gps_distance_m           INTEGER,
    delay_s                  INTEGER,
    event_type               TEXT,
    timetable_route_id       TEXT,
    timetable_service_id     TEXT,
    timetable_direction_id   INTEGER,
    timetable_edition        TEXT,
    alternatives_json        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matching_evidence_date_vehicle
    ON matching_evidence (service_date, vehicle_ref);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, name: str,
                   declaration: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    # CREATE TABLE IF NOT EXISTS does not upgrade an existing durable DB.
    # These additive migrations are safe for the collector's live database.
    _ensure_column(conn, "timepoint_observations", "is_origin",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "timepoint_observations", "match_tier", "TEXT")
    _ensure_column(conn, "poll_log", "stale",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "poll_log", "evidence_written",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "poll_log", "evidence_dropped",
                   "INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    return conn


def upsert_observation(cur, obs: tuple) -> None:
    """Keep the closest observation for each trip and timing point."""
    cur.execute(
        """INSERT INTO timepoint_observations
               (service_date, operator, route, trip_id, siri_journey_ref,
                stop_sequence, stop_code, scheduled_local, observed_delay_s,
                on_time, gps_distance_m, recorded_at, vehicle_ref, is_origin,
                match_tier)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(service_date, trip_id, stop_sequence) DO UPDATE SET
               observed_delay_s = excluded.observed_delay_s,
               on_time          = excluded.on_time,
               gps_distance_m   = excluded.gps_distance_m,
               recorded_at      = excluded.recorded_at,
               vehicle_ref      = excluded.vehicle_ref,
               route            = excluded.route,
               operator         = excluded.operator,
               siri_journey_ref = excluded.siri_journey_ref,
               scheduled_local  = excluded.scheduled_local,
               is_origin        = excluded.is_origin,
               match_tier       = excluded.match_tier
           WHERE excluded.gps_distance_m < timepoint_observations.gps_distance_m""",
        obs)


def _bounded_text(value, limit: int = 256) -> str | None:
    if value is None:
        return None
    return str(value)[:limit]


def write_matching_evidence(
        conn: sqlite3.Connection, evidence: dict, *,
        daily_limit: int = MATCHING_EVIDENCE_DAILY_LIMIT,
        total_limit: int = MATCHING_EVIDENCE_TOTAL_LIMIT) -> str:
    """Store one private anomaly receipt without allowing unbounded growth.

    The raw SIRI document is deliberately never accepted here. Callers pass a
    small allow-list of normalised fields. Repeated snapshots are idempotent,
    each service day has a hard admission limit, and the total table behaves
    as a ring buffer so recent evidence remains available.

    Returns ``written``, ``duplicate``, ``daily_limit`` or ``total_pruned``.
    """
    if daily_limit < 1 or total_limit < 1:
        raise ValueError("matching-evidence limits must be positive")
    service_date = _bounded_text(evidence.get("service_date"), 8)
    captured_at = _bounded_text(evidence.get("captured_at"), 64)
    if not service_date or not captured_at:
        raise ValueError("matching evidence needs service_date and captured_at")

    reasons = sorted({_bounded_text(reason, 64) for reason in
                      evidence.get("reasons", []) if reason})
    calculation_reasons = sorted({
        _bounded_text(reason, 64) for reason in
        evidence.get("calculation_reasons", []) if reason})
    chosen = evidence.get("chosen") or {}
    alternatives = list(evidence.get("alternatives") or [])[
        :MATCHING_EVIDENCE_ALTERNATIVES_LIMIT]
    identity = "\x1f".join(str(value or "") for value in (
        evidence.get("vehicle_ref"), evidence.get("recorded_at"),
        evidence.get("chosen_trip_id"), ",".join(reasons)))
    evidence_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]

    if conn.execute(
            "SELECT 1 FROM matching_evidence WHERE evidence_id=?",
            (evidence_id,)).fetchone():
        return "duplicate"
    admitted_today = conn.execute(
        "SELECT COUNT(*) FROM matching_evidence WHERE service_date=?",
        (service_date,)).fetchone()[0]
    if admitted_today >= daily_limit:
        return "daily_limit"

    cursor = conn.execute(
        """INSERT INTO matching_evidence (
               evidence_id, captured_at, service_date, reasons_json,
               calculation_reasons_json, operator, route, vehicle_ref,
               direction, journey_ref, origin_aimed_departure, recorded_at,
               lat, lon, bearing, block_ref, chosen_trip_id, match_tier,
               candidate_count, candidates_truncated, gps_distance_m, delay_s,
               event_type, timetable_route_id, timetable_service_id,
               timetable_direction_id, timetable_edition, alternatives_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (evidence_id, captured_at, service_date,
         json.dumps(reasons, separators=(",", ":")),
         json.dumps(calculation_reasons, separators=(",", ":")),
         _bounded_text(evidence.get("operator")),
         _bounded_text(evidence.get("route")),
         _bounded_text(evidence.get("vehicle_ref")),
         _bounded_text(evidence.get("direction")),
         _bounded_text(evidence.get("journey_ref")),
         _bounded_text(evidence.get("origin_aimed_departure"), 64),
         _bounded_text(evidence.get("recorded_at"), 64),
         evidence.get("lat"), evidence.get("lon"), evidence.get("bearing"),
         _bounded_text(evidence.get("block_ref")),
         _bounded_text(evidence.get("chosen_trip_id")),
         _bounded_text(evidence.get("match_tier"), 16),
         evidence.get("candidate_count"),
         int(bool(evidence.get("candidates_truncated"))),
         evidence.get("gps_distance_m"), evidence.get("delay_s"),
         _bounded_text(evidence.get("event_type"), 32),
         _bounded_text(chosen.get("route_id")),
         _bounded_text(chosen.get("service_id")),
         chosen.get("direction_id"),
         _bounded_text(chosen.get("timetable_edition"), 32),
         json.dumps(alternatives, ensure_ascii=True,
                    separators=(",", ":"))))
    if not cursor.rowcount:
        return "duplicate"

    count = conn.execute(
        "SELECT COUNT(*) FROM matching_evidence").fetchone()[0]
    overflow = count - total_limit
    if overflow > 0:
        conn.execute(
            """DELETE FROM matching_evidence WHERE evidence_id IN (
                   SELECT evidence_id FROM matching_evidence
                   ORDER BY captured_at, evidence_id LIMIT ?)""",
            (overflow,))
        return "total_pruned"
    return "written"


def log_poll(conn, poll_at_iso: str, ok: bool, totals: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO poll_log
               (poll_at, ok, vehicles_total, candidates, matched,
                obs_written, dropped_insane, stale, evidence_written,
                evidence_dropped)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (poll_at_iso, int(ok), totals.get("vehicles_total", 0),
         totals.get("candidates", 0), totals.get("matched", 0),
         totals.get("obs_written", 0), totals.get("dropped_insane", 0),
         totals.get("stale", 0), totals.get("evidence_written", 0),
         totals.get("evidence_dropped", 0)))

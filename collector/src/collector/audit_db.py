"""Write the audit database schema consumed by rollup and export jobs.

Schema changes must be coordinated with every downstream reader and the
published methodology.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MATCHING_EVIDENCE_DAILY_LIMIT = 250
MATCHING_EVIDENCE_TOTAL_LIMIT = 5000
MATCHING_EVIDENCE_ALTERNATIVES_LIMIT = 3
MATCHING_EVIDENCE_CELL_LIMIT = 10
MATCHING_EVIDENCE_PER_JOURNEY_LIMIT = 2
MATCHING_EVIDENCE_PER_OPERATOR_CELL_LIMIT = 4
MATCHING_EVIDENCE_OPERATORS = frozenset({
    "FBRI", "SCGL", "LEMB", "ABUS", "CTCO", "TYSW",
})
MATCHING_EVIDENCE_TIME_ZONE = ZoneInfo("Europe/London")
MATCHING_EVIDENCE_REASON_PRIORITY = (
    "direction_changed_within_run",
    "match_changed_within_run",
    "sanity_rejected",
    "extreme_delay",
)

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
    evidence_dropped INTEGER NOT NULL DEFAULT 0,
    evidence_deduplicated INTEGER NOT NULL DEFAULT 0,
    evidence_scope_dropped INTEGER NOT NULL DEFAULT 0,
    evidence_quota_dropped INTEGER NOT NULL DEFAULT 0,
    evidence_errors INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS matching_evidence (
    evidence_id              TEXT PRIMARY KEY,
    captured_at              TEXT NOT NULL,
    service_date             TEXT NOT NULL,
    sampling_date            TEXT,
    sampling_band            TEXT,
    sampling_reason          TEXT,
    reasons_json             TEXT NOT NULL,
    calculation_reasons_json TEXT NOT NULL,
    operator                 TEXT,
    route                    TEXT,
    vehicle_ref              TEXT,
    direction                TEXT,
    journey_ref              TEXT,
    origin_aimed_departure   TEXT,
    origin_ref               TEXT,
    destination_ref          TEXT,
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
    _ensure_column(conn, "poll_log", "evidence_deduplicated",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "poll_log", "evidence_scope_dropped",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "poll_log", "evidence_quota_dropped",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "poll_log", "evidence_errors",
                   "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "matching_evidence", "sampling_date", "TEXT")
    _ensure_column(conn, "matching_evidence", "sampling_band", "TEXT")
    _ensure_column(conn, "matching_evidence", "sampling_reason", "TEXT")
    _ensure_column(conn, "matching_evidence", "origin_ref", "TEXT")
    _ensure_column(conn, "matching_evidence", "destination_ref", "TEXT")
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_matching_evidence_sampling
           ON matching_evidence
              (sampling_date, sampling_band, sampling_reason)""")
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


def _parse_evidence_time(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _sampling_slot(evidence: dict, reasons: list[str]) -> tuple[str, str, str] | None:
    """Return local date, four-hour band and deterministic primary reason.

    The feed's recorded timestamp is authoritative for sampling. A valid
    capture timestamp is a fail-safe fallback; the matched timetable service
    date is deliberately never used because a bad match can put it tomorrow.
    """
    instant = (_parse_evidence_time(evidence.get("recorded_at"))
               or _parse_evidence_time(evidence.get("captured_at")))
    if instant is None:
        return None
    local = instant.astimezone(MATCHING_EVIDENCE_TIME_ZONE)
    start = (local.hour // 4) * 4
    band = f"{start:02d}-{start + 3:02d}"
    reason = next(
        (candidate for candidate in MATCHING_EVIDENCE_REASON_PRIORITY
         if candidate in reasons),
        "other",
    )
    return local.strftime("%Y%m%d"), band, reason


def write_matching_evidence(
        conn: sqlite3.Connection, evidence: dict, *,
        daily_limit: int = MATCHING_EVIDENCE_DAILY_LIMIT,
        total_limit: int = MATCHING_EVIDENCE_TOTAL_LIMIT,
        cell_limit: int = MATCHING_EVIDENCE_CELL_LIMIT,
        per_journey_limit: int = MATCHING_EVIDENCE_PER_JOURNEY_LIMIT,
        per_operator_cell_limit: int = (
            MATCHING_EVIDENCE_PER_OPERATOR_CELL_LIMIT)) -> str:
    """Store one private anomaly receipt without allowing unbounded growth.

    The raw SIRI document is deliberately never accepted here. Callers pass a
    small allow-list of normalised fields. Repeated snapshots are idempotent.
    Admission is spread across local capture-time bands and reason categories,
    noisy journeys are capped, and the total table behaves as a ring buffer so
    recent evidence remains available.

    The returned status explains whether the row was written, deduplicated or
    rejected by a specific hard boundary.
    """
    if min(daily_limit, total_limit, cell_limit, per_journey_limit,
           per_operator_cell_limit) < 1:
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

    operator = _bounded_text(evidence.get("operator"))
    if operator not in MATCHING_EVIDENCE_OPERATORS:
        return "operator_out_of_scope"
    slot = _sampling_slot(evidence, reasons)
    if slot is None:
        return "invalid_timestamp"
    sampling_date, sampling_band, sampling_reason = slot

    # Keep the original whole-day ceiling as a second hard safety boundary.
    # Legacy rows from before sampling metadata use capture date for this one
    # migration day, so a restart cannot silently double the allowance.
    local_start = datetime.strptime(sampling_date, "%Y%m%d").replace(
        tzinfo=MATCHING_EVIDENCE_TIME_ZONE)
    local_end = local_start + timedelta(days=1)
    admitted_today = conn.execute(
        """SELECT COUNT(*) FROM matching_evidence
           WHERE sampling_date=?
              OR (sampling_date IS NULL
                  AND julianday(captured_at)>=julianday(?)
                  AND julianday(captured_at)<julianday(?))""",
        (sampling_date, local_start.isoformat(),
         local_end.isoformat())).fetchone()[0]
    if admitted_today >= daily_limit:
        return "daily_limit"

    vehicle_ref = _bounded_text(evidence.get("vehicle_ref"))
    chosen_trip_id = _bounded_text(evidence.get("chosen_trip_id"))
    journey_count = conn.execute(
        """SELECT COUNT(*) FROM matching_evidence
           WHERE sampling_date=? AND sampling_reason=?
             AND coalesce(vehicle_ref,'')=coalesce(?, '')
             AND coalesce(chosen_trip_id,'')=coalesce(?, '')""",
        (sampling_date, sampling_reason, vehicle_ref,
         chosen_trip_id)).fetchone()[0]
    if journey_count >= per_journey_limit:
        return "journey_duplicate"

    operator_cell_count = conn.execute(
        """SELECT COUNT(*) FROM matching_evidence
           WHERE sampling_date=? AND sampling_band=? AND sampling_reason=?
             AND operator=?""",
        (sampling_date, sampling_band, sampling_reason, operator)).fetchone()[0]
    if operator_cell_count >= per_operator_cell_limit:
        return "operator_cell_limit"

    cell_count = conn.execute(
        """SELECT COUNT(*) FROM matching_evidence
           WHERE sampling_date=? AND sampling_band=? AND sampling_reason=?""",
        (sampling_date, sampling_band, sampling_reason)).fetchone()[0]
    if cell_count >= cell_limit:
        return "cell_limit"
    if cell_count == cell_limit - 1:
        journey_ref = _bounded_text(evidence.get("journey_ref"))
        seen_reference = conn.execute(
            """SELECT 1 FROM matching_evidence
               WHERE sampling_date=? AND sampling_band=? AND sampling_reason=?
                 AND coalesce(operator,'')=coalesce(?, '')
                 AND coalesce(route,'')=coalesce(?, '')
                 AND coalesce(journey_ref,'')=coalesce(?, '')
               LIMIT 1""",
            (sampling_date, sampling_band, sampling_reason, operator,
             _bounded_text(evidence.get("route")), journey_ref)).fetchone()
        if not journey_ref or seen_reference:
            return "reserved_slot"

    columns = (
        "evidence_id", "captured_at", "service_date", "sampling_date",
        "sampling_band", "sampling_reason", "reasons_json",
        "calculation_reasons_json", "operator", "route", "vehicle_ref",
        "direction", "journey_ref", "origin_aimed_departure", "origin_ref",
        "destination_ref", "recorded_at", "lat", "lon", "bearing",
        "block_ref", "chosen_trip_id", "match_tier", "candidate_count",
        "candidates_truncated", "gps_distance_m", "delay_s", "event_type",
        "timetable_route_id", "timetable_service_id",
        "timetable_direction_id", "timetable_edition", "alternatives_json",
    )
    values = (
         evidence_id, captured_at, service_date, sampling_date, sampling_band,
         sampling_reason,
         json.dumps(reasons, separators=(",", ":")),
         json.dumps(calculation_reasons, separators=(",", ":")),
         operator,
         _bounded_text(evidence.get("route")),
         vehicle_ref,
         _bounded_text(evidence.get("direction")),
         _bounded_text(evidence.get("journey_ref")),
         _bounded_text(evidence.get("origin_aimed_departure"), 64),
         _bounded_text(evidence.get("origin_ref")),
         _bounded_text(evidence.get("destination_ref")),
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
                    separators=(",", ":")))
    cursor = conn.execute(
        f"INSERT INTO matching_evidence ({','.join(columns)}) "
        f"VALUES ({','.join('?' for _ in columns)})",
        values)
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
                evidence_dropped, evidence_deduplicated,
                evidence_scope_dropped, evidence_quota_dropped,
                evidence_errors)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (poll_at_iso, int(ok), totals.get("vehicles_total", 0),
         totals.get("candidates", 0), totals.get("matched", 0),
         totals.get("obs_written", 0), totals.get("dropped_insane", 0),
         totals.get("stale", 0), totals.get("evidence_written", 0),
         totals.get("evidence_dropped", 0),
         totals.get("evidence_deduplicated", 0),
         totals.get("evidence_scope_dropped", 0),
         totals.get("evidence_quota_dropped", 0),
         totals.get("evidence_errors", 0)))

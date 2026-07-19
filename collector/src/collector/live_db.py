"""live.db — the collector's output database, read by the site and the bot.

Contract from COLLECTOR_SPEC.md §5/§6:
- vehicles: one row per vehicle, current state, aged out by readers at 90 s.
- events:   corroborated delay events for the bot. The collector only writes
  an event when consecutive polls agree (ordinary >=2, extreme >=3), and
  re-emits for the same vehicle+journey only when the delay materially
  worsens (>= 300 s beyond the last emitted). Observed numbers only.
- situations: SIRI-SX disruptions, upserted by (situation_number), replaced
  when version increases, closed when absent from the latest poll.
- poller_status: one row per feed for /healthz and the circuit breaker.

All delays are integer seconds. WAL mode; single writer (the collector),
many readers.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CORROBORATION_ORDINARY = 2
CORROBORATION_EXTREME = 3
EXTREME_LATE_S = 30 * 60
EXTREME_EARLY_S = -15 * 60
REEMIT_WORSEN_S = 300
AGREE_TOLERANCE_S = 120  # consecutive polls "agree" within ±2 min

# Bump whenever the schema below changes. A version mismatch rebuilds live.db,
# which repopulates vehicle state on the next poll but discards queued events.
# Any version bump therefore requires an explicit compatibility and event-loss
# review.
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_ref TEXT PRIMARY KEY,
    operator_ref TEXT NOT NULL,
    line TEXT,
    direction TEXT,
    destination TEXT,
    journey_ref TEXT,
    trip_id TEXT,
    match_tier TEXT,
    origin_aimed_departure TEXT,
    recorded_at TEXT,
    lat REAL, lon REAL,
    bearing REAL,
    block_ref TEXT,
    delay_seconds INTEGER,
    low_confidence INTEGER,
    event_type TEXT,
    stop_code TEXT,
    stop_sequence INTEGER,
    distance_m INTEGER,
    at_depot TEXT,
    -- corroboration bookkeeping (collector-internal)
    streak_event_type TEXT,
    streak_count INTEGER DEFAULT 0,
    streak_delay_s INTEGER,
    last_emitted_delay_s INTEGER,
    last_emitted_journey TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vehicles_updated ON vehicles(updated_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    vehicle_ref TEXT, operator_ref TEXT, line TEXT, direction TEXT,
    journey_ref TEXT, origin_aimed_departure TEXT,
    stop_code TEXT, stop_name TEXT,
    delay_seconds INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    source TEXT NOT NULL,              -- 'timing_point' | 'live_estimate'
    corroboration INTEGER NOT NULL,
    lat REAL, lon REAL,
    block_ref TEXT,
    low_confidence INTEGER,
    consumed_by_bot_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_unconsumed
    ON events(consumed_by_bot_at) WHERE consumed_by_bot_at IS NULL;

CREATE TABLE IF NOT EXISTS situations (
    situation_number TEXT PRIMARY KEY,
    version INTEGER,
    participant TEXT,
    progress TEXT,
    planned INTEGER,
    reason TEXT,
    summary TEXT,
    description TEXT,
    advice TEXT,
    severity TEXT,
    validity_start TEXT, validity_end TEXT,
    versioned_at TEXT,
    link TEXT,
    affected_json TEXT,
    closed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poller_status (
    name TEXT PRIMARY KEY,
    last_attempt_at TEXT,
    last_success_at TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    circuit_state TEXT DEFAULT 'ok'
);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    found = conn.execute("PRAGMA user_version").fetchone()[0]
    if found != SCHEMA_VERSION:
        # covers pre-versioning files too (they report 0 but may hold old
        # tables); drops are no-ops on a genuinely fresh database
        logging.getLogger(__name__).warning(
            "live.db schema v%s != v%s: rebuilding (live state repopulates "
            "within one poll)", found, SCHEMA_VERSION)
        conn.executescript(
            "DROP TABLE IF EXISTS vehicles; DROP TABLE IF EXISTS events;"
            "DROP TABLE IF EXISTS situations; DROP TABLE IF EXISTS poller_status;")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EventDecision:
    emit: bool
    corroboration: int
    reason: str  # for logging/tests: 'threshold' | 'worsened' | ...


def _is_extreme(delay_s: int) -> bool:
    return delay_s >= EXTREME_LATE_S or delay_s <= EXTREME_EARLY_S


def _journey_run_id(journey_ref: str, origin_aimed_departure: str | None,
                    recorded_at: str | None) -> str:
    """Identity for one physical run, not merely a reusable HHMM ref.

    Many operators reuse DatedVehicleJourneyRef values every day. Including
    the dated origin timestamp prevents yesterday's corroboration streak or
    emission from suppressing today's run. Feeds without an origin timestamp
    fall back to the observation date, which still prevents cross-day reuse.
    """
    anchor = origin_aimed_departure or (recorded_at or "")[:10]
    return f"{journey_ref}\x1f{anchor}" if anchor else journey_ref


def decide_event(prev_row, event_type: str, delay_s: int, journey_ref: str,
                 origin_aimed_departure: str | None = None,
                 recorded_at: str | None = None) -> EventDecision:
    """Pure corroboration logic. prev_row is the vehicle's previous DB row
    (sqlite3.Row or None). Returns whether to emit an event and the streak."""
    if event_type == "punctual":
        return EventDecision(False, 0, "punctual")

    prev_type = prev_row["streak_event_type"] if prev_row else None
    prev_delay = prev_row["streak_delay_s"] if prev_row else None
    prev_count = prev_row["streak_count"] if prev_row else 0
    current_run = _journey_run_id(
        journey_ref, origin_aimed_departure, recorded_at)
    prev_run = (_journey_run_id(
        prev_row["journey_ref"], prev_row["origin_aimed_departure"],
        prev_row["recorded_at"])
        if prev_row else None)

    # Corroboration is PER JOURNEY: a new run of the same vehicle starts
    # from scratch, otherwise yesterday's streak vouches for today's bus.
    same_journey = prev_run == current_run

    if same_journey and prev_type == event_type and prev_delay is not None \
            and abs(delay_s - prev_delay) <= AGREE_TOLERANCE_S:
        streak = (prev_count or 0) + 1
    else:
        streak = 1

    needed = CORROBORATION_EXTREME if _is_extreme(delay_s) else CORROBORATION_ORDINARY
    if streak < needed:
        return EventDecision(False, streak, "building")

    last_emitted = prev_row["last_emitted_delay_s"] if prev_row else None
    last_journey = prev_row["last_emitted_journey"] if prev_row else None
    if last_emitted is not None and last_journey == current_run:
        if abs(delay_s) < abs(last_emitted) + REEMIT_WORSEN_S:
            return EventDecision(False, streak, "already-emitted")
        return EventDecision(True, streak, "worsened")
    return EventDecision(True, streak, "threshold")


def upsert_vehicle(conn: sqlite3.Connection, snap, est, match, *,
                   destination: str = "", at_depot: str | None = None) -> EventDecision:
    """Write one vehicle's state; returns the event decision (event row is
    written here too when due). snap=VehicleSnapshot, est=LiveEstimate|None,
    match=Match|None."""
    prev = conn.execute("SELECT * FROM vehicles WHERE vehicle_ref = ?",
                        (snap.vehicle_ref,)).fetchone()

    if est is None:
        decision = EventDecision(False, 0, "no-estimate")
        streak_type, streak_count, streak_delay = None, 0, None
        emitted_delay = prev["last_emitted_delay_s"] if prev else None
        emitted_journey = prev["last_emitted_journey"] if prev else None
        delay_s, low_conf, ev_type, stop_code = None, None, None, None
        stop_seq = dist_m = None
    else:
        current_run = _journey_run_id(
            snap.journey_ref, snap.origin_aimed_departure,
            snap.recorded_utc.isoformat())
        decision = decide_event(
            prev, est.event_type, est.delay_s, snap.journey_ref,
            snap.origin_aimed_departure, snap.recorded_utc.isoformat())
        streak_type = est.event_type if est.event_type != "punctual" else None
        streak_count = decision.corroboration
        streak_delay = est.delay_s if streak_type else None
        emitted_delay = prev["last_emitted_delay_s"] if prev else None
        emitted_journey = prev["last_emitted_journey"] if prev else None
        if decision.emit:
            emitted_delay, emitted_journey = est.delay_s, current_run
        delay_s, low_conf = est.delay_s, int(est.low_confidence)
        ev_type, stop_code = est.event_type, est.stop_code
        stop_seq = est.stop_sequence
        dist_m = est.distance_m

    conn.execute(
        """INSERT INTO vehicles (vehicle_ref, operator_ref, line, direction,
               destination, journey_ref, trip_id, match_tier,
               origin_aimed_departure, recorded_at, lat, lon, bearing,
               block_ref, delay_seconds, low_confidence, event_type, stop_code,
               stop_sequence, distance_m, at_depot, streak_event_type, streak_count,
               streak_delay_s, last_emitted_delay_s, last_emitted_journey,
               updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(vehicle_ref) DO UPDATE SET
               operator_ref=excluded.operator_ref, line=excluded.line,
               direction=excluded.direction, destination=excluded.destination,
               journey_ref=excluded.journey_ref, trip_id=excluded.trip_id,
               match_tier=excluded.match_tier,
               origin_aimed_departure=excluded.origin_aimed_departure,
               recorded_at=excluded.recorded_at, lat=excluded.lat,
               lon=excluded.lon, bearing=excluded.bearing,
               block_ref=excluded.block_ref, delay_seconds=excluded.delay_seconds,
               low_confidence=excluded.low_confidence,
               event_type=excluded.event_type, stop_code=excluded.stop_code,
               stop_sequence=excluded.stop_sequence, distance_m=excluded.distance_m,
               at_depot=excluded.at_depot,
               streak_event_type=excluded.streak_event_type,
               streak_count=excluded.streak_count,
               streak_delay_s=excluded.streak_delay_s,
               last_emitted_delay_s=excluded.last_emitted_delay_s,
               last_emitted_journey=excluded.last_emitted_journey,
               updated_at=excluded.updated_at""",
        (snap.vehicle_ref, snap.operator_ref, snap.line, snap.direction,
         destination, snap.journey_ref,
         match.trip_id if match else None,
         match.tier if match else None,
         snap.origin_aimed_departure,
         snap.recorded_utc.isoformat(), snap.lat, snap.lon, snap.bearing,
         snap.block_ref, delay_s, low_conf, ev_type, stop_code, stop_seq,
         dist_m, at_depot, streak_type, streak_count, streak_delay,
         emitted_delay, emitted_journey, _now_iso()))

    if decision.emit and est is not None:
        conn.execute(
            """INSERT INTO events (created_at, vehicle_ref, operator_ref, line,
                   direction, journey_ref, origin_aimed_departure, stop_code,
                   stop_name, delay_seconds, event_type, source, corroboration,
                   lat, lon, block_ref, low_confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_now_iso(), snap.vehicle_ref, snap.operator_ref, snap.line,
             snap.direction, snap.journey_ref, snap.origin_aimed_departure,
             est.stop_code, getattr(est, "stop_name", None), est.delay_s,
             est.event_type, "live_estimate",
             decision.corroboration, snap.lat, snap.lon, snap.block_ref,
             int(est.low_confidence)))
    return decision


EVENT_RETENTION_DAYS = 7


def prune_consumed_events(conn: sqlite3.Connection,
                          retention_days: int = EVENT_RETENTION_DAYS) -> int:
    """Delete events the bot has already consumed once they pass the
    retention window. The events table is a message queue, not a log: a
    consumed row's job is done and nothing reads it again. The window keeps
    a week of the bot's inbox around for debugging. Unconsumed rows are
    never deleted, whatever their age. Returns the number of rows removed."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=retention_days)).isoformat()
    with conn:
        cur = conn.execute(
            "DELETE FROM events WHERE consumed_by_bot_at IS NOT NULL "
            "AND created_at < ?", (cutoff,))
    return cur.rowcount


def record_poll(conn: sqlite3.Connection, name: str, ok: bool) -> None:
    now = _now_iso()
    conn.execute(
        """INSERT INTO poller_status (name, last_attempt_at, last_success_at,
               consecutive_failures, circuit_state)
           VALUES (?, ?, CASE WHEN ? THEN ? END, CASE WHEN ? THEN 0 ELSE 1 END, 'ok')
           ON CONFLICT(name) DO UPDATE SET
               last_attempt_at=excluded.last_attempt_at,
               last_success_at=CASE WHEN ? THEN excluded.last_attempt_at
                                    ELSE poller_status.last_success_at END,
               consecutive_failures=CASE WHEN ? THEN 0
                   ELSE poller_status.consecutive_failures + 1 END""",
        (name, now, ok, now, ok, ok, ok))

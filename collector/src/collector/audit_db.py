"""Write the audit database schema consumed by rollup and export jobs.

Schema changes must be coordinated with every downstream reader and the
published methodology.
"""
from __future__ import annotations

import sqlite3

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
    stale           INTEGER NOT NULL DEFAULT 0
);
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
    _ensure_column(conn, "poll_log", "stale",
                   "INTEGER NOT NULL DEFAULT 0")
    conn.commit()
    return conn


def upsert_observation(cur, obs: tuple) -> None:
    """Keep the closest observation for each trip and timing point."""
    cur.execute(
        """INSERT INTO timepoint_observations
               (service_date, operator, route, trip_id, siri_journey_ref,
                stop_sequence, stop_code, scheduled_local, observed_delay_s,
                on_time, gps_distance_m, recorded_at, vehicle_ref, is_origin)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
               is_origin        = excluded.is_origin
           WHERE excluded.gps_distance_m < timepoint_observations.gps_distance_m""",
        obs)


def log_poll(conn, poll_at_iso: str, ok: bool, totals: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO poll_log
               (poll_at, ok, vehicles_total, candidates, matched,
                obs_written, dropped_insane, stale)
           VALUES (?,?,?,?,?,?,?,?)""",
        (poll_at_iso, int(ok), totals.get("vehicles_total", 0),
         totals.get("candidates", 0), totals.get("matched", 0),
         totals.get("obs_written", 0), totals.get("dropped_insane", 0),
         totals.get("stale", 0)))

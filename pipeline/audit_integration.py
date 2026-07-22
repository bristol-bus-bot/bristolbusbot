#!/usr/bin/env python3
"""Build the audit products consumed by the live site and rare-event handoff.

This runs immediately after the completed-day rollup while the audit job is
networkless.  It is the only process that queries raw audit observations for
these products.  The live site and bot receive a small materialised JSON file;
neither queries the audit archive in its request/posting path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from audit_operators import OPERATOR_NAMES, SHOW_OPERATORS


DISTANCE_GATE_M = 150
ON_TIME_LOW_S = -60
ON_TIME_HIGH_S = 359
INITIAL_START = "20260714"
PROFILE_DAYS = 56
PROFILE_MIN_DAYS = 2
PROFILE_MIN_READINGS = 30
HEADLINE_MIN_READINGS = 30
RARE_LOOKBACK_DAYS = 56
RARE_MIN_VEHICLE_DAYS = 20
RARE_MIN_ROUTE_DAYS = 10
RARE_MAX_PAIR_DAYS = 2
RARE_ABSENT_DAYS = 14
RARE_MIN_READINGS = 3
RARE_MIN_POINTS = 3

HERE = Path(__file__).resolve().parent
DEFAULT_AUDIT_DB = Path(os.getenv("BBB_AUDIT_DB", HERE / "audit.db"))
DEFAULT_OUTPUT = Path(os.getenv(
    "BBB_AUDIT_INTEGRATION_PENDING",
    "/var/lib/bristolbusbot/collector/audit_integration.pending.json",
))


def _placeholders(values) -> str:
    return ",".join("?" for _ in values)


def _slug(operator: str, vehicle_ref: str) -> str:
    digest = hashlib.sha256(
        f"{operator}\0{vehicle_ref}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{operator.lower()}-{digest}"


def _event_id(service_date: str, operator: str, vehicle_ref: str,
              route: str) -> str:
    return hashlib.sha256(
        f"{service_date}\0{operator}\0{vehicle_ref}\0{route}".encode("utf-8")
    ).hexdigest()[:24]


def _month_start(through_date: str) -> str:
    # The first measurement month begins on the initial complete service date.
    if through_date.startswith("202607"):
        return INITIAL_START
    return through_date[:6] + "01"


def _completed_dates(cur: sqlite3.Cursor, through_date: str) -> list[str]:
    rows = cur.execute(
        """SELECT DISTINCT service_date FROM daily_overall_summary
           WHERE operator = 'ALL' AND service_date <= ?
           ORDER BY service_date""",
        (through_date,),
    ).fetchall()
    return [row[0] for row in rows]


def _headline(cur: sqlite3.Cursor, start_date: str,
              through_date: str) -> dict:
    ops = _placeholders(SHOW_OPERATORS)
    row = cur.execute(
        f"""SELECT COUNT(*) AS readings,
                   SUM(CASE WHEN observed_delay_s BETWEEN ? AND ?
                            THEN 1 ELSE 0 END) AS on_time,
                   SUM(CASE WHEN observed_delay_s < ? THEN 1 ELSE 0 END) AS early,
                   SUM(CASE WHEN observed_delay_s > ? THEN 1 ELSE 0 END) AS late
            FROM timepoint_observations
            WHERE service_date BETWEEN ? AND ?
              AND operator IN ({ops})
              AND observed_delay_s IS NOT NULL
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?""",
        (ON_TIME_LOW_S, ON_TIME_HIGH_S, ON_TIME_LOW_S, ON_TIME_HIGH_S,
         start_date, through_date, *SHOW_OPERATORS, DISTANCE_GATE_M),
    ).fetchone()
    readings = int(row["readings"] or 0)
    on_time = int(row["on_time"] or 0)
    return {
        "measurement_start": start_date,
        "through_date": through_date,
        "readings": readings,
        "on_time": on_time,
        "early": int(row["early"] or 0),
        "late": int(row["late"] or 0),
        "on_time_pct": round(100 * on_time / readings, 1) if readings else None,
        "minimum_readings": HEADLINE_MIN_READINGS,
        "eligible": readings >= HEADLINE_MIN_READINGS,
    }


def _profile_dates(completed_dates: list[str]) -> list[str]:
    eligible = [value for value in completed_dates if value >= INITIAL_START]
    return eligible[-PROFILE_DAYS:]


def _profiles(cur: sqlite3.Cursor, completed_dates: list[str]) -> list[dict]:
    dates = _profile_dates(completed_dates)
    if not dates:
        return []
    date_ph = _placeholders(dates)
    op_ph = _placeholders(SHOW_OPERATORS)
    rows = cur.execute(
        f"""SELECT operator, vehicle_ref,
                   MIN(service_date) AS first_date,
                   MAX(service_date) AS last_date,
                   COUNT(DISTINCT service_date) AS observed_days,
                   COUNT(*) AS readings,
                   SUM(CASE WHEN observed_delay_s BETWEEN ? AND ?
                            THEN 1 ELSE 0 END) AS on_time,
                   SUM(CASE WHEN observed_delay_s < ? THEN 1 ELSE 0 END) AS early,
                   SUM(CASE WHEN observed_delay_s > ? THEN 1 ELSE 0 END) AS late
            FROM timepoint_observations
            WHERE service_date IN ({date_ph})
              AND operator IN ({op_ph})
              AND vehicle_ref IS NOT NULL AND trim(vehicle_ref) != ''
              AND route IS NOT NULL AND trim(route) != ''
              AND observed_delay_s IS NOT NULL
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?
            GROUP BY operator, vehicle_ref
            HAVING COUNT(DISTINCT service_date) >= ? AND COUNT(*) >= ?
            ORDER BY operator, vehicle_ref""",
        (ON_TIME_LOW_S, ON_TIME_HIGH_S, ON_TIME_LOW_S, ON_TIME_HIGH_S,
         *dates, *SHOW_OPERATORS, DISTANCE_GATE_M,
         PROFILE_MIN_DAYS, PROFILE_MIN_READINGS),
    ).fetchall()

    profiles: dict[tuple[str, str], dict] = {}
    slugs: set[str] = set()
    for row in rows:
        operator, vehicle_ref = row["operator"], row["vehicle_ref"]
        slug = _slug(operator, vehicle_ref)
        if slug in slugs:
            raise RuntimeError(f"vehicle profile slug collision: {slug}")
        slugs.add(slug)
        readings = int(row["readings"])
        on_time = int(row["on_time"] or 0)
        profiles[(operator, vehicle_ref)] = {
            "slug": slug,
            "operator": operator,
            "operator_name": OPERATOR_NAMES.get(operator, operator),
            # This materialised file stays on the Pi.  The ref is needed to
            # join the aggregate to fresh live state; no movements are exposed.
            "vehicle_ref": vehicle_ref,
            "measurement_start": row["first_date"],
            "through_date": row["last_date"],
            "observed_days": int(row["observed_days"]),
            "readings": readings,
            "on_time": on_time,
            "early": int(row["early"] or 0),
            "late": int(row["late"] or 0),
            "on_time_pct": round(100 * on_time / readings, 1),
            "routes": [],
        }

    if not profiles:
        return []

    route_rows = cur.execute(
        f"""SELECT operator, vehicle_ref, route,
                   COUNT(DISTINCT service_date) AS observed_days,
                   COUNT(*) AS readings,
                   SUM(CASE WHEN observed_delay_s BETWEEN ? AND ?
                            THEN 1 ELSE 0 END) AS on_time,
                   SUM(CASE WHEN observed_delay_s < ? THEN 1 ELSE 0 END) AS early,
                   SUM(CASE WHEN observed_delay_s > ? THEN 1 ELSE 0 END) AS late
            FROM timepoint_observations
            WHERE service_date IN ({date_ph})
              AND operator IN ({op_ph})
              AND vehicle_ref IS NOT NULL
              AND route IS NOT NULL AND trim(route) != ''
              AND observed_delay_s IS NOT NULL
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?
            GROUP BY operator, vehicle_ref, route
            ORDER BY operator, vehicle_ref, readings DESC, route""",
        (ON_TIME_LOW_S, ON_TIME_HIGH_S, ON_TIME_LOW_S, ON_TIME_HIGH_S,
         *dates, *SHOW_OPERATORS, DISTANCE_GATE_M),
    ).fetchall()
    routes: dict[tuple[str, str, str], dict] = {}
    for row in route_rows:
        profile = profiles.get((row["operator"], row["vehicle_ref"]))
        if profile is not None:
            readings = int(row["readings"])
            on_time = int(row["on_time"] or 0)
            route = {
                "route": row["route"],
                "observed_days": int(row["observed_days"]),
                "readings": readings,
                "on_time": on_time,
                "early": int(row["early"] or 0),
                "late": int(row["late"] or 0),
                "on_time_pct": round(100 * on_time / readings, 1),
                "days": [],
            }
            profile["routes"].append(route)
            routes[(row["operator"], row["vehicle_ref"], row["route"])] = route

    day_rows = cur.execute(
        f"""SELECT operator, vehicle_ref, route, service_date,
                   COUNT(*) AS readings,
                   SUM(CASE WHEN observed_delay_s BETWEEN ? AND ?
                            THEN 1 ELSE 0 END) AS on_time,
                   SUM(CASE WHEN observed_delay_s < ? THEN 1 ELSE 0 END) AS early,
                   SUM(CASE WHEN observed_delay_s > ? THEN 1 ELSE 0 END) AS late
            FROM timepoint_observations
            WHERE service_date IN ({date_ph})
              AND operator IN ({op_ph})
              AND vehicle_ref IS NOT NULL
              AND route IS NOT NULL AND trim(route) != ''
              AND observed_delay_s IS NOT NULL
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?
            GROUP BY operator, vehicle_ref, route, service_date
            ORDER BY operator, vehicle_ref, route, service_date DESC""",
        (ON_TIME_LOW_S, ON_TIME_HIGH_S, ON_TIME_LOW_S, ON_TIME_HIGH_S,
         *dates, *SHOW_OPERATORS, DISTANCE_GATE_M),
    ).fetchall()
    for row in day_rows:
        route = routes.get((row["operator"], row["vehicle_ref"], row["route"]))
        if route is None:
            continue
        readings = int(row["readings"])
        on_time = int(row["on_time"] or 0)
        route["days"].append({
            "service_date": row["service_date"],
            "readings": readings,
            "on_time": on_time,
            "early": int(row["early"] or 0),
            "late": int(row["late"] or 0),
            "on_time_pct": round(100 * on_time / readings, 1),
        })
    return list(profiles.values())


def _init_evidence_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rare_working_evidence (
               event_id TEXT PRIMARY KEY,
               evaluated_at TEXT NOT NULL,
               service_date TEXT NOT NULL,
               operator TEXT NOT NULL,
               vehicle_ref TEXT NOT NULL,
               route TEXT NOT NULL,
               profile_slug TEXT,
               vehicle_days INTEGER NOT NULL,
               route_days INTEGER NOT NULL,
               pair_days INTEGER NOT NULL,
               recent_pair_days INTEGER NOT NULL,
               candidate_readings INTEGER NOT NULL,
               candidate_points INTEGER NOT NULL,
               queued INTEGER NOT NULL,
               evidence_json TEXT NOT NULL
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_rare_working_pair_date
           ON rare_working_evidence
              (operator, vehicle_ref, route, service_date)"""
    )
    conn.commit()


def _group_days(cur: sqlite3.Cursor, dates: list[str], columns: str,
                group_by: str) -> dict[tuple, int]:
    if not dates:
        return {}
    rows = cur.execute(
        f"""SELECT {columns}, COUNT(DISTINCT service_date) AS days
            FROM timepoint_observations
            WHERE service_date IN ({_placeholders(dates)})
              AND operator IN ({_placeholders(SHOW_OPERATORS)})
              AND vehicle_ref IS NOT NULL AND trim(vehicle_ref) != ''
              AND route IS NOT NULL AND trim(route) != ''
              AND observed_delay_s IS NOT NULL
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?
            GROUP BY {group_by}""",
        (*dates, *SHOW_OPERATORS, DISTANCE_GATE_M),
    ).fetchall()
    return {tuple(row[name.strip()] for name in columns.split(",")):
            int(row["days"]) for row in rows}


def _rare_workings(conn: sqlite3.Connection, completed_dates: list[str],
                   profiles: list[dict], now_iso: str) -> dict:
    _init_evidence_table(conn)
    if not completed_dates:
        return {"mode": "shadow", "status": "no_completed_day",
                "baseline_days": 0, "events": []}

    candidate_date = completed_dates[-1]
    prior = completed_dates[:-1][-RARE_LOOKBACK_DAYS:]
    base = {
        "mode": "shadow",
        "candidate_date": candidate_date,
        "baseline_days": len(prior),
        "required_baseline_days": RARE_LOOKBACK_DAYS,
        "events": [],
    }
    if len(prior) < RARE_LOOKBACK_DAYS:
        base["status"] = "insufficient_baseline"
        return base

    cur = conn.cursor()
    op_ph = _placeholders(SHOW_OPERATORS)
    candidates = cur.execute(
        f"""SELECT operator, vehicle_ref, route, COUNT(*) AS readings,
                   COUNT(DISTINCT stop_code) AS points
            FROM timepoint_observations
            WHERE service_date = ? AND operator IN ({op_ph})
              AND vehicle_ref IS NOT NULL AND trim(vehicle_ref) != ''
              AND route IS NOT NULL AND trim(route) != ''
              AND stop_code IS NOT NULL AND trim(stop_code) != ''
              AND observed_delay_s IS NOT NULL
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?
            GROUP BY operator, vehicle_ref, route
            HAVING COUNT(*) >= ? AND COUNT(DISTINCT stop_code) >= ?""",
        (candidate_date, *SHOW_OPERATORS, DISTANCE_GATE_M,
         RARE_MIN_READINGS, RARE_MIN_POINTS),
    ).fetchall()

    vehicle_days = _group_days(cur, prior, "operator,vehicle_ref",
                               "operator,vehicle_ref")
    route_days = _group_days(cur, prior, "operator,route", "operator,route")
    pair_days = _group_days(cur, prior, "operator,vehicle_ref,route",
                            "operator,vehicle_ref,route")
    recent_pair_days = _group_days(
        cur, prior[-RARE_ABSENT_DAYS:], "operator,vehicle_ref,route",
        "operator,vehicle_ref,route")
    profile_slugs = {
        (p["operator"], p["vehicle_ref"]): p["slug"] for p in profiles
    }

    accepted = 0
    for row in candidates:
        operator, vehicle_ref, route = (
            row["operator"], row["vehicle_ref"], row["route"])
        key = (operator, vehicle_ref, route)
        stats = {
            "vehicle_days": vehicle_days.get((operator, vehicle_ref), 0),
            "route_days": route_days.get((operator, route), 0),
            "pair_days": pair_days.get(key, 0),
            "recent_pair_days": recent_pair_days.get(key, 0),
            "candidate_readings": int(row["readings"]),
            "candidate_points": int(row["points"]),
        }
        if not (
            stats["vehicle_days"] >= RARE_MIN_VEHICLE_DAYS
            and stats["route_days"] >= RARE_MIN_ROUTE_DAYS
            and stats["pair_days"] <= RARE_MAX_PAIR_DAYS
            and stats["recent_pair_days"] == 0
        ):
            continue

        event_id = _event_id(candidate_date, operator, vehicle_ref, route)
        cooldown_start = (
            datetime.strptime(candidate_date, "%Y%m%d").date()
            - timedelta(days=6)
        ).strftime("%Y%m%d")
        prior_queued = cur.execute(
            """SELECT 1 FROM rare_working_evidence
               WHERE operator = ? AND vehicle_ref = ? AND route = ?
                 AND queued = 1 AND service_date BETWEEN ? AND ?
                 AND event_id != ? LIMIT 1""",
            (operator, vehicle_ref, route, cooldown_start,
             candidate_date, event_id),
        ).fetchone()
        queued = 0 if prior_queued else 1
        evidence = {
            "service_date": candidate_date,
            "operator": operator,
            "vehicle_ref": vehicle_ref,
            "route": route,
            "baseline_dates": [prior[0], prior[-1]],
            **stats,
            "thresholds": {
                "lookback_days": RARE_LOOKBACK_DAYS,
                "minimum_vehicle_days": RARE_MIN_VEHICLE_DAYS,
                "minimum_route_days": RARE_MIN_ROUTE_DAYS,
                "maximum_pair_days": RARE_MAX_PAIR_DAYS,
                "absent_days": RARE_ABSENT_DAYS,
                "minimum_readings": RARE_MIN_READINGS,
                "minimum_points": RARE_MIN_POINTS,
            },
            "cooldown_passed": bool(queued),
        }
        cur.execute(
            """INSERT INTO rare_working_evidence
                   (event_id, evaluated_at, service_date, operator, vehicle_ref,
                    route, profile_slug, vehicle_days, route_days, pair_days,
                    recent_pair_days, candidate_readings, candidate_points,
                    queued, evidence_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(event_id) DO UPDATE SET
                   evaluated_at=excluded.evaluated_at,
                   profile_slug=excluded.profile_slug,
                   evidence_json=excluded.evidence_json""",
            (event_id, now_iso, candidate_date, operator, vehicle_ref, route,
             profile_slugs.get((operator, vehicle_ref)),
             stats["vehicle_days"], stats["route_days"], stats["pair_days"],
             stats["recent_pair_days"], stats["candidate_readings"],
             stats["candidate_points"], queued,
             json.dumps(evidence, sort_keys=True, separators=(",", ":"))),
        )
        accepted += 1

    conn.commit()
    event_rows = cur.execute(
        """SELECT event_id, service_date, operator, vehicle_ref, route,
                  profile_slug, evidence_json
           FROM rare_working_evidence
           WHERE service_date = ? AND queued = 1 ORDER BY operator, route""",
        (candidate_date,),
    ).fetchall()
    base.update({
        "status": "ready",
        "evaluated_candidates": len(candidates),
        "accepted_candidates": accepted,
        "events": [{
            "event_id": row["event_id"],
            "service_date": row["service_date"],
            "operator": row["operator"],
            "vehicle_ref": row["vehicle_ref"],
            "route": row["route"],
            "profile_slug": row["profile_slug"],
            "evidence": json.loads(row["evidence_json"]),
        } for row in event_rows],
    })
    return base


def build_payload(conn: sqlite3.Connection, through_date: str,
                  *, now: datetime | None = None) -> dict:
    conn.row_factory = sqlite3.Row
    completed = _completed_dates(conn.cursor(), through_date)
    if not completed:
        raise RuntimeError(f"no completed audit rollup on or before {through_date}")
    actual_through = completed[-1]
    profiles = _profiles(conn.cursor(), completed)
    generated = (now or datetime.now(timezone.utc)).astimezone(
        timezone.utc).isoformat()
    return {
        "schema": 1,
        "generated_at": generated,
        "published_at": None,
        "source": "independent WECA timing-point audit",
        "audit_url": "https://bristol-bus-bot.github.io/weca-bus-audit/",
        "live_url": "https://bristolbuses.live/",
        "on_time_definition": {"minimum_delay_s": ON_TIME_LOW_S,
                               "maximum_delay_s": ON_TIME_HIGH_S},
        "distance_gate_m": DISTANCE_GATE_M,
        "headline": _headline(conn.cursor(), _month_start(actual_through),
                              actual_through),
        "profiles": profiles,
        "rare_workings": _rare_workings(conn, completed, profiles, generated),
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.",
                                     dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--through", required=True,
                        help="latest completed service date (YYYYMMDD)")
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    datetime.strptime(args.through, "%Y%m%d")
    conn = sqlite3.connect(args.audit_db)
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        payload = build_payload(conn, args.through)
    finally:
        conn.close()
    write_atomic(args.output, payload)
    print(
        f"Wrote {args.output}: {payload['headline']['readings']} headline "
        f"readings, {len(payload['profiles'])} profiles, rare status "
        f"{payload['rare_workings']['status']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

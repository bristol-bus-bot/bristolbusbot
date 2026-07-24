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
"""

import os
import sys
import json
import sqlite3
import statistics
from datetime import datetime, timedelta
from dateutil import tz

from audit_operators import SHOW_OPERATORS, NETWORK_LABEL
from audit_geo import load_geo_index, geo_for
from audit_fleet import load_fleet_index, fleet_for, fleet_number

HERE = os.path.dirname(os.path.abspath(__file__))
AUDIT_DB = os.getenv("BBB_AUDIT_DB", os.path.join(HERE, "audit.db"))

TARGET_TZ = tz.gettz("Europe/London") or tz.tzlocal()

DISTANCE_GATE_M = 150
ON_TIME_LOW_S = -60
ON_TIME_HIGH_S = 359
RAW_RETENTION_DAYS = 95
MIN_GEO_MATCH_PCT = 90.0

DELAY_BUCKETS = [
    "early_5plus",
    "early_1_5",
    "on_time",
    "late_6_10",
    "late_10_20",
    "late_20plus",
]

PEAK_BANDS = ["am_peak", "interpeak", "pm_peak", "evening"]


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


def rollup(conn, date_str, operators, label):
    cur = conn.cursor()
    op_ph = ",".join("?" for _ in operators)

    cur.execute(
        f"""SELECT route, observed_delay_s, gps_distance_m, scheduled_local
           FROM timepoint_observations
           WHERE service_date = ? AND operator IN ({op_ph})""",
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

    cur.execute(
        f"""SELECT route, COUNT(*) FROM expected_trips
           WHERE service_date = ? AND operator IN ({op_ph}) GROUP BY route""",
        (date_str, *operators),
    )
    expected_by_route = {route: count for route, count in cur.fetchall()}

    cur.execute(
        f"""SELECT route, COUNT(DISTINCT trip_id) FROM timepoint_observations
           WHERE service_date = ? AND operator IN ({op_ph}) GROUP BY route""",
        (date_str, *operators),
    )
    observed_by_route = {route: count for route, count in cur.fetchall()}

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
        expected = expected_by_route.get(route, 0)
        observed = observed_by_route.get(route, 0)
        coverage = round(100.0 * observed / expected, 1) if expected else None

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
    expected_total = sum(expected_by_route.values())
    observed_total = sum(observed_by_route.values())
    coverage_total = round(100.0 * observed_total / expected_total, 1) if expected_total else None

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
    if count:
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
    coverage = f"{result['coverage_pct']}%" if result["coverage_pct"] is not None else "n/a"
    print(
        f"  coverage: {result['observed']} of {result['expected']} scheduled trips observed "
        f"({coverage})  [proxy, not proven cancellations]"
    )


def rollup_geo(conn, date_str, operators, label, geo_index):
    """Aggregate in-gate readings by WECA area and ward, for the given operator
    set, into daily_geo_summary. Additive; does not touch the route rollup."""
    cur = conn.cursor()
    op_ph = ",".join("?" for _ in operators)
    cur.execute(
        f"""SELECT stop_code, observed_delay_s FROM timepoint_observations
            WHERE service_date = ? AND operator IN ({op_ph})
              AND gps_distance_m IS NOT NULL AND gps_distance_m <= ?""",
        (date_str, *operators, DISTANCE_GATE_M),
    )
    buckets = {}
    for stop_code, delay_s in cur.fetchall():
        g = geo_for(geo_index, stop_code)
        if not g:
            continue
        for geo_type, geo_key in (("area", g["area"]), ("ward", g["ward"])):
            acc = buckets.setdefault((geo_type, geo_key), {"delays": [], "on_time": 0})
            acc["delays"].append(delay_s)
            if ON_TIME_LOW_S <= delay_s <= ON_TIME_HIGH_S:
                acc["on_time"] += 1

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
    conn.commit()
    return len(buckets)


def geography_match_stats(conn, date_str, operators, geo_index):
    """Measure lookup coverage before any summary rows are changed."""
    cur = conn.cursor()
    op_ph = ",".join("?" for _ in operators)
    cur.execute(
        f"""SELECT stop_code FROM timepoint_observations
            WHERE service_date = ? AND operator IN ({op_ph})
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
        return

    raw_args = sys.argv[1:]
    no_prune = "--no-prune" in raw_args
    positional = [arg for arg in raw_args if not arg.startswith("--")]

    try:
        date_str = resolve_date(positional)
    except ValueError:
        print(f"ERROR: date must be YYYYMMDD, got '{positional[0]}'")
        return

    conn = sqlite3.connect(AUDIT_DB)
    init_summary_tables(conn)
    geo_index = load_geo_index()
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
        print_report(rollup(conn, date_str, [op], op))
    print(f"[{NETWORK_LABEL}] whole network")
    print_report(rollup(conn, date_str, SHOW_OPERATORS, NETWORK_LABEL))

    for op in SHOW_OPERATORS:
        rollup_geo(conn, date_str, [op], op, geo_index)
    n = rollup_geo(conn, date_str, SHOW_OPERATORS, NETWORK_LABEL, geo_index)
    match_text = (
        f"{geo_match['matched']}/{geo_match['eligible']} readings matched"
        if geo_match["eligible"] else "no eligible readings"
    )
    print(f"  geography: {n} area/ward groups rolled up; {match_text}.")

    fleet_index = load_fleet_index()
    if fleet_index:
        for op in SHOW_OPERATORS:
            rollup_fleet(conn, date_str, [op], op, fleet_index)
        n = rollup_fleet(conn, date_str, SHOW_OPERATORS, NETWORK_LABEL, fleet_index)
        print(f"  fleet: {n} models rolled up.")
    else:
        print("  fleet: fbribuses.json not found, skipped.")

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


if __name__ == "__main__":
    main()

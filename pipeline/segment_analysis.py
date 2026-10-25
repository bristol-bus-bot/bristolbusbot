#!/usr/bin/env python3
"""
Where does the timetable not fit? Time lost or gained between timing points.

Investigation for bristolbusbot-planning#78. Laptop only: run it against COPIES
of audit.db and the timetable database, never the Pi's live files. It writes
nothing to either database.

    python segment_analysis.py audit-copy.db timetable-copy.db --out segments.csv
    python segment_analysis.py audit-copy.db timetable-copy.db --from 20261001 --to 20261024

For each journey it pairs the readings at two timing points that are next to
each other in that journey's own timetable. The change in lateness between
them is the time the bus lost (+) or gained (-) on that stretch. A missing
reading breaks the chain: pairs never bridge a timing point without a reading.

Groups are route, direction, stretch (from stop -> to stop), weekday or weekend,
and time band (by the scheduled time at the first stop). Each group reports:
journeys, the timetable's minutes for the stretch, typical real minutes, the
median time lost or gained, and the share of journeys that lost more than two
minutes or gained more than one. Groups with fewer than --min-journeys (30, the
audit's low-sample rule) are left out.

The result is about the timetable and the service, not about drivers.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DISTANCE_GATE_M = 150
MIN_JOURNEYS = 30
LOST_S = 120
GAINED_S = 60
BANDS = (
    ("early", 0, 7), ("am_peak", 7, 10), ("interpeak", 10, 16),
    ("pm_peak", 16, 19), ("evening", 19, 24),
)


def band_for(hour):
    for name, start, end in BANDS:
        if start <= hour < end:
            return name
    return None


def day_type(service_date):
    return "weekend" if datetime.strptime(service_date, "%Y%m%d").weekday() >= 5 else "weekday"


def parse_local(value):
    try:
        return datetime.fromisoformat(str(value).replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def read_only(path):
    conn = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def timing_points(timetable, trip_ids):
    """trip_id -> (direction, ordered list of timing-point stop_sequences)."""
    result = {}
    ids = list(trip_ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        rows = timetable.execute(
            f"""SELECT st.trip_id, st.stop_sequence, t.direction_id
                  FROM stop_times st JOIN trips t ON t.trip_id = st.trip_id
                 WHERE st.trip_id IN ({marks}) AND COALESCE(st.timepoint, 1) = 1
                 ORDER BY st.trip_id, st.stop_sequence""", chunk)
        for row in rows:
            direction, seqs = result.setdefault(row["trip_id"], (row["direction_id"], []))
            seqs.append(row["stop_sequence"])
    return result


def segment_pairs(audit, timetable, date_from=None, date_to=None):
    """Return (one dict per measurable stretch of one journey, tally)."""
    where, args = ["COALESCE(is_origin, 0) = 0", "gps_distance_m <= ?"], [DISTANCE_GATE_M]
    if date_from:
        where.append("service_date >= ?"); args.append(date_from)
    if date_to:
        where.append("service_date <= ?"); args.append(date_to)
    columns = {r[1] for r in audit.execute("PRAGMA table_info(timepoint_observations)")}
    if "is_origin" not in columns:
        where = where[1:]
    rows = audit.execute(
        f"""SELECT service_date, operator, route, trip_id, stop_sequence, stop_code,
                   scheduled_local, observed_delay_s
              FROM timepoint_observations WHERE {' AND '.join(where)}
             ORDER BY service_date, trip_id, stop_sequence""", args).fetchall()
    journeys = defaultdict(list)
    for row in rows:
        if row["observed_delay_s"] is not None:
            journeys[(row["service_date"], row["trip_id"])].append(row)
    plan = timing_points(timetable, {trip for _, trip in journeys})
    tally = {"readings": len(rows), "journeys": len(journeys), "pairs": 0,
             "no_timetable": 0, "gap_skipped": 0, "bad_times": 0}
    pairs = []
    for (service_date, trip_id), readings in journeys.items():
        if trip_id not in plan:
            tally["no_timetable"] += 1
            continue
        direction, seqs = plan[trip_id]
        position = {seq: i for i, seq in enumerate(seqs)}
        for a, b in zip(readings, readings[1:]):
            if a["stop_sequence"] not in position or b["stop_sequence"] not in position:
                tally["gap_skipped"] += 1
                continue
            if position[b["stop_sequence"]] - position[a["stop_sequence"]] != 1:
                tally["gap_skipped"] += 1
                continue
            start, end = parse_local(a["scheduled_local"]), parse_local(b["scheduled_local"])
            if not start or not end or end < start:
                tally["bad_times"] += 1
                continue
            planned_s = (end - start).total_seconds()
            change_s = b["observed_delay_s"] - a["observed_delay_s"]
            tally["pairs"] += 1
            pairs.append({
                "operator": a["operator"], "route": a["route"], "direction": direction,
                "from_stop": a["stop_code"], "to_stop": b["stop_code"],
                "day_type": day_type(service_date), "band": band_for(start.hour),
                "planned_s": planned_s, "actual_s": planned_s + change_s, "change_s": change_s,
            })
    return pairs, tally


def summarise(pairs, min_journeys=MIN_JOURNEYS):
    groups = defaultdict(list)
    for p in pairs:
        key = (p["operator"], p["route"], p["direction"], p["from_stop"], p["to_stop"],
               p["day_type"], p["band"])
        groups[key].append(p)
    out = []
    for key, items in groups.items():
        if len(items) < min_journeys:
            continue
        changes = [p["change_s"] for p in items]
        out.append({
            "operator": key[0], "route": key[1], "direction": key[2],
            "from_stop": key[3], "to_stop": key[4], "day_type": key[5], "band": key[6],
            "journeys": len(items),
            "timetable_min": round(statistics.median(p["planned_s"] for p in items) / 60, 1),
            "typical_actual_min": round(statistics.median(p["actual_s"] for p in items) / 60, 1),
            "median_change_s": int(statistics.median(changes)),
            "lost_over_2min_pct": round(100 * sum(c > LOST_S for c in changes) / len(changes), 1),
            "gained_over_1min_pct": round(100 * sum(c < -GAINED_S for c in changes) / len(changes), 1),
        })
    out.sort(key=lambda g: -g["median_change_s"])
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("audit_db", help="a laptop copy of audit.db")
    parser.add_argument("timetable_db", help="a laptop copy of the timetable database")
    parser.add_argument("--from", dest="date_from", help="first service date, YYYYMMDD")
    parser.add_argument("--to", dest="date_to", help="last service date, YYYYMMDD")
    parser.add_argument("--min-journeys", type=int, default=MIN_JOURNEYS)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out", help="write every qualifying group to this CSV")
    args = parser.parse_args(argv)
    audit, timetable = read_only(args.audit_db), read_only(args.timetable_db)
    pairs, tally = segment_pairs(audit, timetable, args.date_from, args.date_to)
    groups = summarise(pairs, args.min_journeys)
    print(f"{tally['readings']:,} readings in {tally['journeys']:,} journeys -> "
          f"{tally['pairs']:,} measurable stretches "
          f"({tally['gap_skipped']:,} pairs skipped across a missing timing point, "
          f"{tally['no_timetable']:,} journeys not in this timetable, "
          f"{tally['bad_times']:,} with unusable times).")
    print(f"{len(groups):,} groups with at least {args.min_journeys} journeys.\n")
    header = ("route", "direction", "from_stop", "to_stop", "day_type", "band", "journeys",
              "timetable_min", "typical_actual_min", "median_change_s",
              "lost_over_2min_pct", "gained_over_1min_pct")
    for title, rows in (("Most time lost (timetable too tight)", groups[:args.top]),
                        ("Most time gained (timetable too slack)", groups[::-1][:args.top])):
        print(title)
        for g in rows:
            print("  " + "  ".join(str(g[h]) for h in header))
        print()
    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(groups[0]) if groups else header)
            writer.writeheader()
            writer.writerows(groups)
        print(f"Wrote {len(groups):,} groups to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

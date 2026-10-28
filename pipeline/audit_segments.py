#!/usr/bin/env python3
"""Where the timetable doesn't fit: nightly segment products for the audit.

The rollup stores, for each service day, the time lost or gained on every
stretch between two adjacent timing points (method: segment_analysis.py and
SEGMENT_ANALYSIS.md). The export pools the last 28 published days and applies
the review checks before anything is published:

- reading errors are dropped (a big loss won back on the next stretch, a
  typical running time over three times the timetable, or below zero);
- a result must point the same way in at least 3 of the window's 4 weeks;
- slack on the last stretch into a terminus is recovery time, not slack;
- a "loss" starting where buses are usually early is buses waiting there.

The figures describe the timetable and the service, not drivers.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import datetime

import route_split
import segment_analysis

MIN_JOURNEYS = 30
TIGHT_S = 120
SLACK_S = -60
WAITING_SHARE = 0.5
# Per route, publish the clearest few of each kind; recovery time is counted
# but not listed, since it is deliberate.
KEEP = {"tight": 6, "waiting": 3, "slack": 4}


def init_tables(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS daily_segment_changes (
               service_date   TEXT NOT NULL,
               operator       TEXT NOT NULL,
               route          TEXT NOT NULL,
               direction      INTEGER,
               from_stop      TEXT NOT NULL,
               to_stop        TEXT NOT NULL,
               from_name      TEXT,
               to_name        TEXT,
               band           TEXT NOT NULL,
               to_terminus    INTEGER NOT NULL,
               planned_s      INTEGER NOT NULL,
               early_at_start INTEGER NOT NULL,
               changes_json   TEXT NOT NULL
           )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_segment_date ON daily_segment_changes(service_date, operator)")


def _termini_and_names(timetable, keys):
    """Last timing point of each (operator, route, direction), and stop names."""
    termini = defaultdict(set)
    if not segment_analysis.has_tables(timetable, "routes", "agency", "stops"):
        return termini, {}
    names = dict(timetable.execute("SELECT stop_code, stop_name FROM stops"))
    wanted = {(k[0], k[1]) for k in keys}
    current, last = None, None

    def close():
        if current and (current[1], current[2]) in wanted:
            termini[(current[1], current[2], current[3])].add(last)

    for noc, route, direction, trip, code in timetable.execute(
            """SELECT a.agency_noc, r.route_short_name, t.direction_id, t.trip_id, s.stop_code
                 FROM trips t JOIN routes r ON r.route_id = t.route_id
                 JOIN agency a ON a.agency_id = r.agency_id
                 JOIN stop_times st ON st.trip_id = t.trip_id
                 JOIN stops s ON s.stop_id = st.stop_id
                WHERE COALESCE(st.timepoint, 1) = 1
                ORDER BY t.trip_id, st.stop_sequence"""):
        if current is None or trip != current[0]:
            close()
            current = (trip, noc, route, direction)
        last = code
    close()
    return termini, names


def rollup_day(conn, timetable, date_str, *, commit=True):
    """Replace one service day's stretch measurements. Returns rows written."""
    init_tables(conn)
    pairs, _ = segment_analysis.segment_pairs(conn, timetable, date_str, date_str)
    split = route_split.for_connection(conn)
    groups = defaultdict(list)
    for p in pairs:
        # Stored under the public label ("4 Bath"); the timetable still knows
        # the route by its plain number, which the termini lookup needs.
        label = split.route(p["operator"], p["route"], p.get("trip_id"), p.get("stop_code"))
        groups[(p["operator"], label, p["direction"],
                p["from_stop"], p["to_stop"], p["band"])].append(p)
    termini, names = _termini_and_names(
        timetable, {(k[0], items[0]["route"]) for k, items in groups.items()})
    rows = []
    for (operator, route, direction, a, b, band), items in groups.items():
        rows.append((
            date_str, operator, route, direction, a, b, names.get(a), names.get(b), band,
            int(b in termini.get((operator, items[0]["route"], direction), ())),
            int(statistics.median(p["planned_s"] for p in items)),
            sum(1 for p in items if p["start_delay_s"] < -60),
            json.dumps([int(p["change_s"]) for p in items], separators=(",", ":")),
        ))
    conn.execute("DELETE FROM daily_segment_changes WHERE service_date = ?", (date_str,))
    conn.executemany(
        "INSERT INTO daily_segment_changes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    if commit:
        conn.commit()
    return len(rows)


def _pool(rows, window):
    """Pool per-day rows into (operator, route, direction, from, to, band, day type)."""
    week_of = {d: i // 7 for i, d in enumerate(sorted(window))}
    acc = defaultdict(lambda: {"changes": [], "planned": [], "early": 0,
                               "weekly": defaultdict(list)})
    meta = {}
    for r in rows:
        day_type = ("weekday" if datetime.strptime(r["service_date"], "%Y%m%d").weekday() < 5
                    else "weekend")
        key = (r["operator"], r["route"], r["direction"], r["from_stop"], r["to_stop"],
               r["band"], day_type)
        changes = json.loads(r["changes_json"])
        entry = acc[key]
        entry["changes"].extend(changes)
        entry["planned"].append(r["planned_s"])
        entry["early"] += r["early_at_start"]
        entry["weekly"][week_of[r["service_date"]]].extend(changes)
        meta[key[:5]] = (r["from_name"] or r["from_stop"], r["to_name"] or r["to_stop"],
                         bool(r["to_terminus"]))
    stats = {}
    for key, e in acc.items():
        n = len(e["changes"])
        if n < MIN_JOURNEYS:
            continue
        stats[key] = {
            "n": n,
            "median": statistics.median(e["changes"]),
            "planned": statistics.median(e["planned"]),
            "lost": sum(c > TIGHT_S for c in e["changes"]) / n,
            "gained": sum(c < SLACK_S for c in e["changes"]) / n,
            "early_start": e["early"] / n,
            "weekly": [statistics.median(v) for v in e["weekly"].values() if len(v) >= 5],
        }
    return stats, meta, len(set(week_of.values()))


def classify(key, s, stats, meta, weeks):
    """Reviewed verdict for one pooled group, or None if it isn't publishable."""
    kind = "tight" if s["median"] > TIGHT_S else "slack" if s["median"] < SLACK_S else None
    if not kind:
        return None
    typical = s["planned"] + s["median"]
    if typical < 0 or (kind == "tight" and s["median"] > 600
                       and typical > 3 * max(s["planned"], 60)):
        return None
    # Lost on this stretch and won straight back on the next: a bad reading.
    for other_key, other in stats.items():
        if (other_key[:3] == key[:3] and other_key[3] == key[4] and other_key[5:] == key[5:]
                and abs(s["median"]) >= 180 and other["median"] * s["median"] < 0
                and abs(other["median"]) >= 0.6 * abs(s["median"])):
            return None
    needed = min(3, weeks)
    agree = [v for v in s["weekly"] if (v > 60 if kind == "tight" else v < -30)]
    if len(s["weekly"]) < needed or len(agree) < needed or len(agree) < len(s["weekly"]) - 1:
        return None
    terminus = meta[key[:5]][2]
    if kind == "slack" and terminus:
        return "recovery"
    if kind == "tight" and s["early_start"] >= WAITING_SHARE:
        return "waiting"
    return kind


def build(conn, window, pooled_label, public_route):
    """Reviewed stretches per public label: {label: {"routes": {...}, "worst": [...]}}."""
    if not window:
        return {}
    placeholders = ",".join("?" for _ in window)
    try:
        rows = conn.execute(
            f"SELECT * FROM daily_segment_changes WHERE service_date IN ({placeholders})",
            window).fetchall()
    except Exception:
        return {}
    stats, meta, weeks = _pool(rows, window)
    out = {}
    for key, s in stats.items():
        kind = classify(key, s, stats, meta, weeks)
        if not kind:
            continue
        operator, route, direction, _, _, band, day_type = key
        from_name, to_name, _ = meta[key[:5]]
        item = {
            "direction": direction, "from": from_name, "to": to_name,
            "from_stop": key[3], "to_stop": key[4],
            "band": band, "day_type": day_type, "kind": kind,
            "timetable_min": round(s["planned"] / 60, 1),
            "typical_min": round((s["planned"] + s["median"]) / 60, 1),
            "journeys": s["n"],
            "share_pct": round(100 * (s["gained"] if kind in ("slack", "recovery") else s["lost"])),
        }
        for label in filter(None, (operator, pooled_label)):
            route_label = public_route(operator, route, label == pooled_label)
            target = out.setdefault(label, {"routes": defaultdict(list), "worst": []})
            target["routes"][route_label].append({**item, "route": route_label})
    for target in out.values():
        for route_label, items in target["routes"].items():
            items.sort(key=lambda i: -abs(i["typical_min"] - i["timetable_min"]))
            kept = []
            for kind, limit in KEEP.items():
                kept.extend([i for i in items if i["kind"] == kind][:limit])
            target.setdefault("recovery_counts", {})[route_label] = sum(
                i["kind"] == "recovery" for i in items)
            target["routes"][route_label] = kept
        target["routes"] = {r: items for r, items in target["routes"].items() if items}
        tight = sorted(
            (i for items in target["routes"].values() for i in items if i["kind"] == "tight"),
            key=lambda i: -(i["typical_min"] - i["timetable_min"]))
        seen, worst = set(), []
        for i in tight:
            if (i["route"], i["from"], i["to"]) not in seen:
                seen.add((i["route"], i["from"], i["to"]))
                worst.append(i)
        target["worst"] = worst[:15]
        target["routes"] = dict(target["routes"])
    return out

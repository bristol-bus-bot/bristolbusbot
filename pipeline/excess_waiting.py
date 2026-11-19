"""Excess waiting time for frequent routes: private rollup (planning #137).

Not published. Frequent services are formally judged by excess waiting time,
not timetable punctuality (see "Frequent services" in the methodology). For
passengers who turn up without a timetable, what matters is the gap between
buses. From the gaps between consecutive buses at a stop:

    waiting time = sum of squared gaps / (2 * sum of gaps)

computed for the timetable's gaps (scheduled) and for the gaps that really
happened (actual). Excess waiting time is actual minus scheduled, in minutes.

A bus we didn't see makes a gap look twice as long, so a stop-hour is used
only when at least ``min_seen`` of its scheduled journeys were seen. The
report says how many stop-hours were kept at each tolerance: that is the
evidence for whether the figure is reliable enough to show.

Scheduled times come from a timetable whose calendars cover the day; actual
times from the audit readings at the same stop, matched to a scheduled time.

    python pipeline/excess_waiting.py <audit.db> <timetable.db> 20261108 20261113
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

FREQUENT_PER_HOUR = 6          # a stop-hour counts as frequent at six or more buses
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TOLERANCES = (1.0, 0.9, 0.8)


def waiting_minutes(times: list[float]) -> float | None:
    """Average wait, in minutes, for passengers arriving at random."""
    times = sorted(times)
    gaps = [later - earlier for earlier, later in zip(times, times[1:]) if later > earlier]
    if not gaps:
        return None
    return sum(gap * gap for gap in gaps) / (2 * sum(gaps)) / 60


def _seconds(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def scheduled_departures(timetable: sqlite3.Connection, day: str,
                         routes: set[str]) -> dict[tuple[str, str], list[int]]:
    """{(route, stop_code): [seconds after service midnight]} for one day."""
    when = datetime.strptime(day, "%Y%m%d").date()
    weekday = WEEKDAYS[when.weekday()]
    active = {row[0] for row in timetable.execute(
        f"SELECT service_id FROM calendar WHERE {weekday} = 1 AND start_date <= ? AND end_date >= ?",
        (day, day))}
    for service, kind in timetable.execute(
            "SELECT service_id, exception_type FROM calendar_dates WHERE date = ?", (day,)):
        (active.add if int(kind) == 1 else active.discard)(service)
    placeholders = ",".join("?" * len(routes))
    found: dict[tuple[str, str], list[int]] = defaultdict(list)
    for route, stop, departure, service in timetable.execute(
            f"""SELECT r.route_short_name, s.stop_code, st.departure_time, t.service_id
                  FROM stop_times st JOIN trips t USING(trip_id) JOIN routes r USING(route_id)
                  JOIN stops s ON s.stop_id = st.stop_id
                 WHERE r.route_short_name IN ({placeholders}) AND st.timepoint = 1
                   AND s.stop_code IS NOT NULL""", tuple(routes)):
        if service in active and departure:
            found[(str(route), str(stop))].append(_seconds(departure))
    return {key: sorted(set(values)) for key, values in found.items()}


def observed_departures(audit: sqlite3.Connection, day: str,
                        routes: set[str]) -> dict[tuple[str, str], dict[int, float]]:
    """{(route, stop_code): {scheduled seconds: actual seconds}} from readings."""
    midnight = datetime.strptime(day, "%Y%m%d")
    placeholders = ",".join("?" * len(routes))
    found: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    for route, stop, scheduled, delay in audit.execute(
            f"""SELECT route, stop_code, scheduled_local, observed_delay_s
                  FROM timepoint_observations
                 WHERE service_date = ? AND route IN ({placeholders})
                   AND COALESCE(is_origin, 0) = 0 AND observed_delay_s IS NOT NULL
                   AND gps_distance_m <= 150 AND stop_code IS NOT NULL""",
            (day, *routes)):
        try:
            local = datetime.fromisoformat(str(scheduled).replace(" ", "T")).replace(tzinfo=None)
        except ValueError:
            continue
        seconds = int((local - midnight).total_seconds())
        found[(str(route), str(stop))][seconds] = seconds + int(delay)
    return found


def stop_hour_results(scheduled: dict, observed: dict) -> list[dict]:
    """One row per frequent stop-hour: seen share, scheduled and actual waits."""
    rows = []
    for key, times in scheduled.items():
        by_hour: dict[int, list[int]] = defaultdict(list)
        for seconds in times:
            by_hour[seconds // 3600].append(seconds)
        seen = observed.get(key, {})
        for hour, hour_times in by_hour.items():
            if len(hour_times) < FREQUENT_PER_HOUR:
                continue
            actual = [seen[s] for s in hour_times if s in seen]
            rows.append({
                "route": key[0], "stop": key[1], "hour": hour,
                "scheduled_journeys": len(hour_times), "seen_journeys": len(actual),
                "scheduled_wait": waiting_minutes(hour_times),
                "actual_wait": waiting_minutes(actual) if len(actual) >= 2 else None,
            })
    return rows


def summarise(rows: list[dict]) -> dict:
    """Pooled per route, at each tolerance for unseen buses."""
    result = {"stop_hours": len(rows), "tolerances": {}}
    for tolerance in TOLERANCES:
        kept = [row for row in rows if row["actual_wait"] is not None
                and row["seen_journeys"] >= tolerance * row["scheduled_journeys"]]
        routes: dict[str, list[dict]] = defaultdict(list)
        for row in kept:
            routes[row["route"]].append(row)
        result["tolerances"][f"{int(tolerance * 100)}% seen"] = {
            "kept_stop_hours": len(kept),
            "kept_share_pct": round(100 * len(kept) / len(rows), 1) if rows else None,
            "routes": {
                route: {
                    "stop_hours": len(items),
                    "scheduled_wait_min": round(sum(i["scheduled_wait"] for i in items) / len(items), 2),
                    "actual_wait_min": round(sum(i["actual_wait"] for i in items) / len(items), 2),
                    "excess_wait_min": round(sum(i["actual_wait"] - i["scheduled_wait"]
                                                 for i in items) / len(items), 2),
                } for route, items in sorted(routes.items())},
        }
    return result


def run(audit_path: Path, timetable_path: Path, first: str, last: str) -> dict:
    audit = sqlite3.connect(f"file:{audit_path}?mode=ro", uri=True)
    timetable = sqlite3.connect(f"file:{timetable_path}?mode=ro", uri=True)
    try:
        rows = []
        day = datetime.strptime(first, "%Y%m%d")
        while day <= datetime.strptime(last, "%Y%m%d"):
            date_str = day.strftime("%Y%m%d")
            labels = [row[0] for row in audit.execute(
                "SELECT DISTINCT route FROM daily_route_class "
                "WHERE service_date = ? AND frequent = 1 AND operator != 'ALL'", (date_str,))]
            routes = {str(label).split(" ")[0] for label in labels}
            if routes:
                rows += [{**row, "service_date": date_str} for row in stop_hour_results(
                    scheduled_departures(timetable, date_str, routes),
                    observed_departures(audit, date_str, routes))]
            day += timedelta(days=1)
    finally:
        audit.close()
        timetable.close()
    return {"from": first, "to": last, **summarise(rows)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("audit", type=Path)
    parser.add_argument("timetable", type=Path)
    parser.add_argument("first")
    parser.add_argument("last")
    args = parser.parse_args()
    print(json.dumps(run(args.audit, args.timetable, args.first, args.last), indent=2))

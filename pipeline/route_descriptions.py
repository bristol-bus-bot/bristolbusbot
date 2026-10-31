"""Where each route goes, in one line built from the timetable.

"Bristol to Clevedon, via Clifton, Failand and Tickenham". For each route the
commonest journey pattern in its busier direction gives the two ends; the
towns of the timing points in between (NaPTAN localities from the stop
enrichment file) give the "via". Nothing is written by hand, so it can't
drift from the timetable.
"""
from __future__ import annotations

from collections import Counter, defaultdict

import route_split
import stop_places

MAX_VIA = 3


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def describe_pattern(stops: list[tuple[str, str]], towns: dict[str, str]) -> str | None:
    """One line for a journey given as [(stop_code, stop_name), ...] in order."""
    if len(stops) < 2:
        return None
    (first_code, first_name), (last_code, last_name) = stops[0], stops[-1]
    first_town, last_town = towns.get(first_code), towns.get(last_code)
    if first_code == last_code or (first_name == last_name and first_town == last_town):
        text = f"Circular from {first_name}" + (f", {first_town}" if first_town else "")
    elif first_town and last_town and first_town != last_town:
        text = f"{first_town} to {last_town}"
    elif first_town and first_town == last_town:
        text = f"{first_name} to {last_name}, {first_town}"
    elif first_name == last_name and (first_town or last_town):
        # Same stop name at both ends but only one town known (the other end is
        # usually outside the area): "Bus Station to Bus Station" says nothing.
        text = f"From {first_name} ({first_town or last_town})"
    else:
        # One end's town is unknown: name both stops, with whichever town we know.
        text = (f"{first_name}" + (f" ({first_town})" if first_town else "")
                + f" to {last_name}" + (f" ({last_town})" if last_town else ""))
    ends = {first_town, last_town}
    via = []
    for code, _ in stops[1:-1]:
        town = towns.get(code)
        if town and town not in ends and town not in via:
            via.append(town)
    if via:
        text += ", via " + _join(via[:MAX_VIA])
    return text


def build(timetable, audit_conn=None, towns: dict[str, str] | None = None) -> dict[tuple[str, str], str]:
    """{(operator, route label): description}. Labels match the audit's own,
    including "4 Bath" / "4 Bristol" where a number is shared."""
    towns = stop_places.load() if towns is None else towns
    split = route_split.for_connection(audit_conn) if audit_conn is not None else route_split.RouteSplit()
    patterns: dict[tuple[str, str, int], Counter] = defaultdict(Counter)
    current, stops = None, []

    def close():
        if current and len(stops) >= 2:
            noc, route, direction, trip = current
            label = split.route(noc, route, trip, stops[0][0])
            patterns[(noc, label, direction)][tuple(stops)] += 1

    try:
        rows = timetable.execute(
            """SELECT t.trip_id, a.agency_noc, r.route_short_name, COALESCE(t.direction_id, 0),
                      s.stop_code, s.stop_name
                 FROM trips t JOIN routes r ON r.route_id = t.route_id
                 JOIN agency a ON a.agency_id = r.agency_id
                 JOIN stop_times st ON st.trip_id = t.trip_id
                 JOIN stops s ON s.stop_id = st.stop_id
                WHERE COALESCE(st.timepoint, 1) = 1
                  AND r.route_short_name IS NOT NULL AND r.route_short_name != ''
                ORDER BY t.trip_id, st.stop_sequence""")
        for trip, noc, route, direction, code, name in rows:
            if current is None or trip != current[3]:
                close()
                current, stops = (noc, route, int(direction), trip), []
            stops.append((str(code or ""), str(name or "").strip()))
        close()
    except Exception:  # noqa: BLE001 - descriptions are optional extras
        return {}

    by_route: dict[tuple[str, str], list[tuple[int, Counter]]] = defaultdict(list)
    for (noc, label, direction), counter in patterns.items():
        by_route[(noc, label)].append((sum(counter.values()), counter))
    out = {}
    for key, directions in by_route.items():
        _, busiest = max(directions, key=lambda item: item[0])
        stops = list(busiest.most_common(1)[0][0])
        text = describe_pattern(stops, towns)
        if text:
            out[key] = text
    return out


def attach(summary: dict, descriptions: dict[tuple[str, str], str], public_route, network_label: str) -> int:
    """Add "description" to every route row in the summary; returns how many."""
    network = {public_route(noc, label, True): text for (noc, label), text in descriptions.items()}
    added = 0
    views = [summary.get("period") or {}, summary.get("latest_day") or {}]
    for view in views:
        for operator, data in (view.get("by_operator") or {}).items():
            for row in data.get("routes") or []:
                text = (network.get(row.get("route")) if operator == network_label
                        else descriptions.get((operator, row.get("route"))))
                if text:
                    row["description"] = text
                    added += 1
    return added

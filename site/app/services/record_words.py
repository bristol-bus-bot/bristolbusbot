"""Plain-English summary of a bus's observed record.

Fixed sentence templates filled with checked numbers, never generated text.
The comparison is with other buses on the same route, day and scheduled hour,
so a bus is judged against the conditions it actually ran in. Wording keeps
the responsibility on the timetable and the service, not the driver.
"""
from __future__ import annotations

SAME_BAND_PTS = 5
MIN_HOUR_READINGS = 30


def _pct(value) -> int:
    return int(round(float(value)))


def _hour(hour: int) -> str:
    return f"{hour:02d}:00"


def summary(profile: dict) -> list[str]:
    on_time = profile.get("on_time_pct")
    if on_time is None:
        return []
    lines = [f"This bus was on time for {_pct(on_time)}% of the timing points we measured it at."]
    comparison = profile.get("comparison") or {}
    others = comparison.get("others_on_time_pct")
    if others is not None:
        lines.append(
            f"Other buses on the same routes, on the same days and at the same times "
            f"of day, were on time {_pct(others)}% of the time.")
        gap = float(on_time) - float(others)
        if abs(gap) < SAME_BAND_PTS:
            lines.append(
                "So it did about as well as the buses around it. When a whole route "
                "runs late, that's down to the timetable, the traffic and how the "
                "service is run, not the person driving.")
        elif gap > 0:
            lines.append("So it did better than the buses around it.")
        else:
            lines.append(
                "So it did worse than the buses around it. One bus's record also "
                "depends on which journeys it was given and how late things already "
                "were when it took over, so this isn't a verdict on its drivers.")
    hours = []
    for hour, counts in (profile.get("hours") or {}).items():
        try:
            hour_number = int(hour)
            on, early, late = (int(v) for v in counts)
        except (TypeError, ValueError):
            continue
        total = on + early + late
        if total >= MIN_HOUR_READINGS:
            hours.append((100 * on / total, hour_number))
    if len(hours) >= 2:
        worst, best = min(hours), max(hours)
        if best[0] - worst[0] >= 15:
            lines.append(
                f"It ran best around {_hour(best[1])} ({_pct(best[0])}% on time) "
                f"and worst around {_hour(worst[1])} ({_pct(worst[0])}%).")
    return lines


def route_comparison(route: dict) -> str | None:
    others = (route.get("comparison") or {}).get("others_on_time_pct")
    if others is None:
        return None
    return f"other buses at the same times {_pct(others)}%"

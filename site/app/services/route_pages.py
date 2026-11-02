"""Search-friendly route pages, built from the audit files the Pi already has.

The nightly audit export writes audit_summary.json (each route's 28-day
figures), hourly/<operator>.json (time of day) and segments/<operator>.json
(where the timetable doesn't fit) into the audit folder. This reads them,
keeps them in memory until they change, and shapes one route's page. Nothing
is stored and no database is queried, so a page costs the Pi next to nothing.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

NETWORK_LABEL = "ALL"
MAX_AGE = timedelta(days=3)
# Short public names where the operator's registered name isn't what people say.
OPERATOR_SHORT = {"FBRI": "First"}
OPERATOR_SLUGS = {"FBRI": "first"}
_SLUG = re.compile(r"[^a-z0-9]+")
WEEKDAY_HOURS = range(5, 24)


def slugify(value: str) -> str:
    text = str(value or "").lower().replace("'", "")
    if text.startswith("the "):
        text = text[4:]
    return _SLUG.sub("-", text).strip("-")


def _usable(row: dict) -> bool:
    """A route has a figure worth showing (the audit's own rule for a number)."""
    status = (row.get("qualification") or {}).get("status")
    return row.get("on_time_pct") is not None and status != "unavailable"


def _ends(description: str | None) -> str | None:
    """"Frenchay to Temple Meads, via ..." -> "Frenchay to Temple Meads"."""
    if not description:
        return None
    return description.split(", via ")[0]


def _in_ten(pct: float) -> int:
    return int(round(float(pct) / 10))


def _minutes(seconds: float | None) -> str | None:
    """Typical lateness, to the nearest minute once it's a minute or more."""
    if seconds is None:
        return None
    sign = "+" if seconds >= 0 else "-"
    total = abs(float(seconds))
    return f"{sign}{int(round(total / 60))} min" if total >= 60 else f"{sign}{int(round(total))} s"


def period_label(start: str, end: str) -> str:
    """"20261004", "20261031" -> "4 to 31 October"."""
    try:
        first = datetime.strptime(start, "%Y%m%d")
        last = datetime.strptime(end, "%Y%m%d")
    except (TypeError, ValueError):
        return ""
    if (first.year, first.month) == (last.year, last.month):
        return f"{first.day} to {last.day} {last:%B}"
    if first.year == last.year:
        return f"{first.day} {first:%B} to {last.day} {last:%B}"
    return f"{first.day} {first:%B} {first.year} to {last.day} {last:%B} {last.year}"


def _hour_label(hour: int) -> str:
    return "midnight" if hour in (0, 24) else "noon" if hour == 12 else (
        f"{hour}am" if hour < 12 else f"{hour - 12}pm")


class _JsonFile:
    """One audit file, reloaded only when it changes and ignored once stale."""

    def __init__(self, path: Path):
        self.path = path
        self._mtime_ns: int | None = None
        self._value: dict | None = None

    def get(self) -> dict | None:
        try:
            stat = self.path.stat()
        except OSError:
            return None
        if datetime.now(timezone.utc) - datetime.fromtimestamp(
                stat.st_mtime, timezone.utc) > MAX_AGE:
            return None
        if stat.st_mtime_ns != self._mtime_ns:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("not an object")
            except (OSError, ValueError) as exc:
                logger.warning("route pages: %s unreadable: %s", self.path.name, exc)
                value = None
            self._mtime_ns, self._value = stat.st_mtime_ns, value
        return self._value


class RoutePages:
    def __init__(self, audit_dir: str):
        self.directory = Path(audit_dir)
        self._summary = _JsonFile(self.directory / "audit_summary.json")
        self._files: dict[tuple[str, str], _JsonFile] = {}
        self._index_key: int | None = None
        self._index: dict[tuple[str, str], dict] = {}

    def _operator_file(self, kind: str, operator: str) -> dict | None:
        if not re.fullmatch(r"[A-Z0-9]{2,8}", operator or ""):
            return None
        key = (kind, operator)
        if key not in self._files:
            self._files[key] = _JsonFile(self.directory / kind / f"{operator}.json")
        return self._files[key].get()

    def _build_index(self) -> dict[tuple[str, str], dict]:
        summary = self._summary.get()
        if summary is None:
            return {}
        if id(summary) == self._index_key:
            return self._index
        names = {str(o.get("code")): str(o.get("name") or o.get("code"))
                 for o in summary.get("operators") or [] if isinstance(o, dict)}
        period = summary.get("period") or {}
        index: dict[tuple[str, str], dict] = {}
        for code, data in (period.get("by_operator") or {}).items():
            if code == NETWORK_LABEL or not isinstance(data, dict):
                continue
            name = names.get(code, code)
            operator_slug = OPERATOR_SLUGS.get(code) or slugify(name)
            for row in data.get("routes") or []:
                label = str(row.get("route") or "")
                route_slug = slugify(label)
                if not label or not route_slug or (operator_slug, route_slug) in index:
                    continue
                index[(operator_slug, route_slug)] = {
                    "operator": code, "operator_name": name,
                    "operator_short": OPERATOR_SHORT.get(code, name),
                    "operator_slug": operator_slug, "route_slug": route_slug,
                    "label": label, "row": row, "usable": _usable(row),
                    "period": {"from": period.get("from"), "to": period.get("to")},
                }
        self._index_key, self._index = id(summary), index
        return index

    def routes(self) -> list[dict]:
        """Every route, grouped for the index page: operator order, then number."""
        def number_key(entry: dict):
            parts = re.split(r"(\d+)", entry["label"])
            return [int(p) if p.isdigit() else p.lower() for p in parts]
        entries = list(self._build_index().values())
        return sorted(entries, key=lambda e: (e["operator"] != "FBRI", e["operator_name"], number_key(e)))

    def path_for(self, operator: str, label: str) -> str | None:
        """The page for one operator's route label, if it has one."""
        for entry in self._build_index().values():
            if entry["operator"] == operator and entry["label"] == label:
                return f"/routes/{entry['operator_slug']}/{entry['route_slug']}"
        return None

    def page(self, operator_slug: str, route_slug: str) -> dict | None:
        entry = self._build_index().get((operator_slug, route_slug))
        if entry is None:
            return None
        row = entry["row"]
        view = {**entry, "ends": _ends(row.get("description")),
                "description": row.get("description")}
        if not entry["usable"]:
            return view
        readings = int(row.get("readings_in_gate") or 0)
        very_late = int(row.get("very_late") or 0)
        late = int(row.get("late") or 0)

        def share(count: int) -> float:
            return round(100.0 * count / readings, 1) if readings else 0.0
        view.update({
            "on_time_pct": row["on_time_pct"],
            "in_ten": _in_ten(row["on_time_pct"]),
            "late_in_ten": _in_ten(share(late)),
            "readings": readings,
            "split": [
                ("early", "Early", share(int(row.get("early") or 0))),
                ("on-time", "On time", float(row["on_time_pct"])),
                ("late", "Late, 6 to 20 min", share(max(late - very_late, 0))),
                ("very-late", "Very late, 20+ min", share(very_late)),
            ],
            "typical": _minutes(row.get("median_delay_s")),
            "journeys": (None if row.get("coverage_withheld") or not row.get("expected_trips")
                         else (int(row.get("observed_trips") or 0), int(row["expected_trips"]))),
        })
        view["hours"], view["worst_hour"] = self._hours(entry)
        view["stretch"] = self._stretch(entry)
        return view

    def _hours(self, entry: dict) -> tuple[list[dict], str | None]:
        payload = self._operator_file("hourly", entry["operator"]) or {}
        weekday = ((payload.get("routes") or {}).get(entry["label"]) or {}).get("weekday") or {}
        minimum = int(payload.get("min_readings") or 30)
        hours = []
        for hour in WEEKDAY_HOURS:
            counts = weekday.get(str(hour))
            if not isinstance(counts, list) or len(counts) != 3:
                continue
            total = sum(int(value or 0) for value in counts)
            if total < minimum:
                continue
            pct = round(100.0 * int(counts[0] or 0) / total)
            hours.append({"hour": hour, "label": _hour_label(hour), "pct": pct,
                          "level": "good" if pct >= 87 else "ok" if pct >= 62 else "poor"})
        worst = min(hours, key=lambda h: h["pct"]) if len(hours) >= 3 else None
        return hours, (f"around {worst['label']}" if worst else None)

    def _stretch(self, entry: dict) -> dict | None:
        """The clearest place the timetable doesn't match how buses run."""
        payload = self._operator_file("segments", entry["operator"]) or {}
        items = [item for item in (payload.get("routes") or {}).get(entry["label"]) or []
                 if isinstance(item, dict) and item.get("kind") in {"tight", "slack"}
                 and item.get("timetable_min") is not None and item.get("typical_min") is not None]
        if not items:
            return None
        best = max(items, key=lambda i: (i["kind"] == "tight",
                                         abs(float(i["typical_min"]) - float(i["timetable_min"]))))

        def place(name, town):
            return f"{name} ({town})" if town else str(name)
        return {
            "kind": best["kind"],
            "from": place(best.get("from"), best.get("from_place")),
            "to": place(best.get("to"), best.get("to_place")),
            "timetable": int(round(float(best["timetable_min"]))),
            "typical": int(round(float(best["typical_min"]))),
            "when": " ".join(filter(None, (
                {"weekday": "weekdays", "weekend": "weekends"}.get(best.get("day_type")),
                {"am_peak": "7 to 10am", "interpeak": "10am to 4pm", "pm_peak": "4 to 7pm",
                 "evening": "after 7pm", "early": "before 7am"}.get(best.get("band"))))),
        }

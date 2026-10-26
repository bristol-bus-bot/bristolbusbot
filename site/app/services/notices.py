"""Source-linked local notices; never infer cancellations or restored service."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

LDN = ZoneInfo("Europe/London")
SOURCE = "https://journeyplanner.travelwest.info/travel-updates"


def _date(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def _link(value):
    try:
        url = urlsplit(value or "")
        if url.scheme == "https" and url.hostname and not url.username:
            return value
    except ValueError:
        pass
    return SOURCE


def _window_status(periods, description, now):
    """Keep separate windows separate; uncertain metadata cannot assert active."""
    windows = []
    uncertain = not periods
    for p in periods:
        if not isinstance(p, dict):
            uncertain = True
            continue
        start, end = _date(p.get("StartTime")), _date(p.get("EndTime"))
        if not start or not end or end <= start or set(p) - {"StartTime", "EndTime"}:
            uncertain = True
            continue
        windows.append((start, end))
    # Explicit clock ranges can contradict the XML envelope (observed in feed).
    clocks = re.search(r"(?:from|between)\s+(\d{1,2}:\d{2})\s*(?:to|and|[-–])\s*(\d{1,2}:\d{2})", description, re.I)
    if clocks and windows:
        expected = tuple(f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" for t in clocks.groups())
        if any((a.astimezone(LDN).strftime("%H:%M"), b.astimezone(LDN).strftime("%H:%M")) != expected for a, b in windows):
            uncertain = True
    if uncertain:
        return "uncertain", windows
    if any(a <= now < b for a, b in windows):
        return "current", windows
    if any(a > now for a, _ in windows):
        return "upcoming", windows
    return "expired", windows


def _feed_check(live, now):
    poll = live.execute("SELECT last_success_at FROM poller_status WHERE name='siri_sx'").fetchone()
    checked = _date(poll[0]) if poll else None
    fresh = bool(checked) and timedelta(0) <= now - checked <= timedelta(minutes=15)
    return fresh, checked


def _published(live, now):
    """Yield (row, affected, status, windows) for every notice we may show."""
    rows = live.execute("SELECT * FROM situations WHERE closed_at IS NULL AND participant='WestofEngland'").fetchall()
    for r in rows:
        # Explicit closure and feed withdrawal both remove a notice, without an
        # all-clear message. Other source lifecycle states are not assumed open.
        if r["progress"] != "open":
            continue
        try:
            affected = json.loads(r["affected_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if not isinstance(affected, dict):
            continue
        # Old collector rows lack this field; do not silently use lossy dates.
        periods = affected.get("validity_periods")
        if not isinstance(periods, list):
            continue
        status, windows = _window_status(periods, r["description"] or "", now)
        if status == "expired":
            continue
        updated = _date(r["versioned_at"])
        if status == "uncertain" and (not updated or now - updated > timedelta(days=30)):
            continue
        if status == "upcoming" and min(a for a, _ in windows if a > now) > now + timedelta(days=14):
            continue
        yield r, affected, status, windows


def _stop_refs(affected):
    return {s.get("stop_ref") for s in affected.get("stops", []) if isinstance(s, dict)}


RANK = {"current": 0, "uncertain": 1, "upcoming": 2}


def for_context(live, timetable, *, stop=None, operator=None, line=None, now=None):
    now = now or datetime.now(timezone.utc)
    fresh, checked = _feed_check(live, now)
    if not fresh:
        return {"notices": [], "available": False, "checked_at": checked.isoformat() if checked else None}
    refs = set()
    if stop:
        refs = {r[0] for r in timetable.execute("SELECT stop_id FROM stops WHERE stop_code=?", (stop,))}
    result = []
    for r, affected, status, windows in _published(live, now):
        if stop:
            matches = bool(_stop_refs(affected) & refs)
        else:
            matches = bool(operator and line) and any(
                s.get("operator") == operator and s.get("line") == line
                for s in affected.get("lines", []) if isinstance(s, dict))
        if not matches:
            continue
        result.append({
            "id": r["situation_number"], "summary": r["summary"] or "Travel notice",
            "description": r["description"] or "", "advice": r["advice"] or "",
            "status": status, "source": "West of England", "url": _link(r["link"]),
            "updated_at": r["versioned_at"],
            "periods": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in sorted(windows) if b > now],
        })
    result.sort(key=lambda n: (RANK[n["status"]], n["summary"]))
    return {"notices": result, "available": True, "checked_at": checked.isoformat()}


def stop_summary(live, timetable, *, now=None):
    """Map each stop code with a published notice to its most pressing status.

    Used to badge map markers before anyone taps a stop. It applies exactly
    the same rules as for_context, so a badge always has a card behind it.
    """
    now = now or datetime.now(timezone.utc)
    fresh, checked = _feed_check(live, now)
    if not fresh:
        return {"stops": {}, "available": False, "checked_at": checked.isoformat() if checked else None}
    by_ref = {}
    for _, affected, status, _ in _published(live, now):
        for ref in _stop_refs(affected):
            if ref and (ref not in by_ref or RANK[status] < RANK[by_ref[ref]]):
                by_ref[ref] = status
    stops = {}
    if by_ref:
        marks = ",".join("?" * len(by_ref))
        for ref, code in timetable.execute(
                f"SELECT stop_id, stop_code FROM stops WHERE stop_id IN ({marks})", list(by_ref)):
            if code and (code not in stops or RANK[by_ref[ref]] < RANK[stops[code]]):
                stops[code] = by_ref[ref]
    return {"stops": stops, "available": True, "checked_at": checked.isoformat()}

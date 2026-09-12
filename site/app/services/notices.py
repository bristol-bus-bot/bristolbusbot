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


def for_context(live, timetable, *, stop=None, operator=None, line=None, now=None):
    now = now or datetime.now(timezone.utc)
    poll = live.execute("SELECT last_success_at FROM poller_status WHERE name='siri_sx'").fetchone()
    checked = _date(poll[0]) if poll else None
    if not checked or not timedelta(0) <= now - checked <= timedelta(minutes=15):
        return {"notices": [], "available": False, "checked_at": checked.isoformat() if checked else None}
    refs = set()
    if stop:
        refs = {r[0] for r in timetable.execute("SELECT stop_id FROM stops WHERE stop_code=?", (stop,))}
    result = []
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
        if stop:
            matches = any(s.get("stop_ref") in refs for s in affected.get("stops", []) if isinstance(s, dict))
        else:
            matches = bool(operator and line) and any(
                s.get("operator") == operator and s.get("line") == line
                for s in affected.get("lines", []) if isinstance(s, dict))
        if not matches:
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
        result.append({
            "id": r["situation_number"], "summary": r["summary"] or "Travel notice",
            "description": r["description"] or "", "advice": r["advice"] or "",
            "status": status, "source": "West of England", "url": _link(r["link"]),
            "updated_at": r["versioned_at"],
            "periods": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in sorted(windows) if b > now],
        })
    result.sort(key=lambda n: ({"current": 0, "uncertain": 1, "upcoming": 2}[n["status"]], n["summary"]))
    return {"notices": result, "available": True, "checked_at": checked.isoformat()}

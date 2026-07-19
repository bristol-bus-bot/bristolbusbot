"""Parse SIRI-VM vehicle activity into the collector's internal fields.

The parser accepts optional namespace prefixes, singleton elements and wrapped
text nodes produced by ``xmltodict``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import re as _re

from .timeparse import parse_iso_utc


def clean_destination(raw_name: str | None) -> str:
    """Normalise underscore-separated names and trailing letter codes."""
    if not raw_name:
        return ""
    name = str(raw_name).replace("_", " ").strip()
    name = _re.sub(r"\s+[A-Z]\s*$", "", name).strip()
    return _re.sub(r"\s+", " ", name)


def get_nested_value(data, path: str):
    """Walk 'A/B/C' through xmltodict output, tolerating siri: prefixes and
    {'#text': ...} wrappers. Returns None on any miss."""
    if data is None:
        return None
    val = data
    for key in path.split("/"):
        if not isinstance(val, dict):
            return None
        found = None
        for p_key in (key, f"siri:{key}"):
            found = val.get(p_key)
            if found is not None:
                break
        val = found
        if val is None:
            return None
    if isinstance(val, dict) and "#text" in val:
        val = val["#text"]
    return val


def activities_from_xmltodict(parsed: dict) -> list:
    """Extract the VehicleActivity list, normalising the one-element case."""
    vm = (parsed.get("Siri", {}) or {}).get("ServiceDelivery", {}).get(
        "VehicleMonitoringDelivery", {})
    if isinstance(vm, list):
        vm = vm[0] if vm else {}
    acts = vm.get("VehicleActivity", [])
    if acts and not isinstance(acts, list):
        acts = [acts]
    return acts or []


def anchor_departure_local(mvj: dict, target_tz, now_local: datetime):
    """Best-effort scheduled first-departure anchor in local time.

    Prefers OriginAimedDepartureTime; falls back to DatedVehicleJourneyRef
    read as HHMM on the nearest calendar day (several operators publish only
    the HHMM ref). Nearest-day selection keeps a 23:50 run observed at 00:20
    anchored to yesterday instead of almost 24 hours in the future.
    Returns (aware datetime | None, 'origin'|'ref'|None).
    """
    origin = parse_iso_utc(get_nested_value(mvj, "OriginAimedDepartureTime"))
    if origin:
        return origin.astimezone(target_tz), "origin"
    ref = str(get_nested_value(
        mvj, "FramedVehicleJourneyRef/DatedVehicleJourneyRef") or "").strip()
    if len(ref) == 4 and ref.isdigit():
        hh, mm = int(ref[:2]), int(ref[2:])
        if hh < 24 and mm < 60:
            today = now_local.replace(hour=hh, minute=mm, second=0,
                                      microsecond=0)
            candidates = (today - timedelta(days=1), today,
                          today + timedelta(days=1))
            return min(candidates,
                       key=lambda candidate: abs(candidate - now_local)), "ref"
    return None, None


@dataclass
class VehicleSnapshot:
    """One vehicle's extracted fields from a single VehicleActivity."""
    vehicle_ref: str
    operator_ref: str
    line: str
    direction: str
    lat: float
    lon: float
    recorded_utc: datetime
    journey_ref: str
    origin_aimed_departure: str | None
    destination_raw: str
    bearing: float | None          # SIRI Bearing where the feed provides it
    block_ref: str | None          # running-board id; PLANNED working, not actual
    origin_stop_ref: str | None
    destination_stop_ref: str | None


def extract_snapshot(activity: dict) -> VehicleSnapshot | None:
    """Pull one VehicleActivity into a typed snapshot; None if unusable.

    'Unusable' = missing any of: MVJ, operator, line, parseable GPS,
    RecordedAtTime. Everything else is optional.
    """
    mvj = get_nested_value(activity, "MonitoredVehicleJourney")
    if not mvj:
        return None
    operator_ref = str(get_nested_value(mvj, "OperatorRef") or "").strip()
    line = str(get_nested_value(mvj, "PublishedLineName")
               or get_nested_value(mvj, "LineRef") or "").strip().rstrip("_")
    if not operator_ref or not line:
        return None
    try:
        lat = float(get_nested_value(mvj, "VehicleLocation/Latitude"))
        lon = float(get_nested_value(mvj, "VehicleLocation/Longitude"))
    except (TypeError, ValueError):
        return None
    recorded_utc = parse_iso_utc(get_nested_value(activity, "RecordedAtTime"))
    if not recorded_utc:
        return None

    bearing = None
    raw_bearing = get_nested_value(mvj, "Bearing")
    if raw_bearing is not None:
        try:
            bearing = float(raw_bearing) % 360.0
        except (TypeError, ValueError):
            bearing = None

    return VehicleSnapshot(
        vehicle_ref=str(get_nested_value(mvj, "VehicleRef") or "").strip(),
        operator_ref=operator_ref,
        line=line,
        direction=str(get_nested_value(mvj, "DirectionRef") or "").lower().strip(),
        lat=lat,
        lon=lon,
        recorded_utc=recorded_utc,
        journey_ref=str(get_nested_value(
            mvj, "FramedVehicleJourneyRef/DatedVehicleJourneyRef") or "").strip(),
        origin_aimed_departure=get_nested_value(mvj, "OriginAimedDepartureTime"),
        destination_raw=str(get_nested_value(mvj, "DestinationName") or ""),
        bearing=bearing,
        block_ref=(str(get_nested_value(mvj, "BlockRef")).strip()
                   if get_nested_value(mvj, "BlockRef") is not None else None),
        origin_stop_ref=(str(get_nested_value(mvj, "OriginRef")).strip()
                         if get_nested_value(mvj, "OriginRef") is not None else None),
        destination_stop_ref=(str(get_nested_value(mvj, "DestinationRef")).strip()
                              if get_nested_value(mvj, "DestinationRef") is not None else None),
    )

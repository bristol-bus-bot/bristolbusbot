"""Build the stable ``/api/buses`` response from collector vehicle rows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .depots import check_depot
from .fleet import Fleet

LATE_THRESHOLD_MIN = 4
EARLY_THRESHOLD_MIN = -3


def _event_type(delay_minutes: int, waiting: bool) -> str:
    if waiting:
        return "waiting"
    if delay_minutes >= LATE_THRESHOLD_MIN:
        return "delayed"
    if delay_minutes <= EARLY_THRESHOLD_MIN:
        return "early"
    return "punctual"


# tests freeze time by patching this module's `datetime`; the ghost check
# compares two stored strings and must stay on the real class
from datetime import datetime as _iso_dt  # noqa: E402


def _is_ghost(recorded_at: str | None, updated_at: str | None,
              max_lag_s: float = 600) -> bool:
    if not recorded_at or not updated_at:
        return False
    try:
        lag = (_iso_dt.fromisoformat(updated_at)
               - _iso_dt.fromisoformat(recorded_at)).total_seconds()
    except ValueError:
        return False
    return lag > max_lag_s


def active_buses(live_conn, fleet: Fleet, stale_seconds: int = 90,
                 now_utc: datetime | None = None) -> list[dict]:
    now = now_utc or datetime.now(timezone.utc)
    cutoff = (now - timedelta(seconds=stale_seconds)).isoformat()
    rows = live_conn.execute(
        "SELECT * FROM vehicles WHERE updated_at > ?", (cutoff,)).fetchall()

    buses = []
    for r in rows:
        # Reject rows whose poll timestamp is fresh but vehicle timestamp is
        # stale. Collector ingest applies the primary form of this check.
        if _is_ghost(r["recorded_at"], r["updated_at"]):
            continue
        delay_s = r["delay_seconds"]
        has_schedule = r["trip_id"] is not None
        delay_min = round(delay_s / 60) if delay_s is not None else 0
        # 'waiting at origin': at the first stop before its departure time
        waiting = bool(has_schedule and r["stop_code"] is not None
                       and r["delay_seconds"] is not None and delay_s < 0
                       and _is_first_stop(r))
        depot = check_depot(r["lat"], r["lon"]) if r["lat"] else None
        state = "depot" if depot else ("waiting" if waiting else "in_service")
        d = fleet.details(r["vehicle_ref"] or "", r["operator_ref"] or "")
        direction = (r["direction"] or "")

        bus = {
            "vehicleRef": r["vehicle_ref"],
            "operatorRef": r["operator_ref"],
            "line": r["line"],
            "destination": r["destination"] or "Unknown",
            "latitude": r["lat"],
            "longitude": r["lon"],
            "delayMinutes": delay_min,
            "eventType": _event_type(delay_min, waiting),
            "waitingAtOrigin": waiting,
            "directionId": 1 if direction in ("inbound", "inb") else 0,
            "journeyCode": r["journey_ref"],
            "tripId": r["trip_id"],  # the collector's own match — display it,
                                     # never re-derive it (re-matching once
                                     # served the wrong town's route)
            "directionRef": direction,
            "originAimedDep": r["origin_aimed_departure"] or "",
            "hasSchedule": has_schedule,
            # bearing straight from the feed (better than deriving it)
            "bearing": r["bearing"],
            "lastStopName": r["stop_code"] or "unknown",  # name filled by caller
            "description": fleet.description(d["fleetNumber"], state),
            "livery": d["livery"],
            "model": d["model"],
            "fleetNumber": d["fleetNumber"],
            "reg": d["reg"],
            **d["extras"],
        }
        if depot:
            bus["atDepot"] = True
            bus["depotName"] = depot
            bus["eventType"] = "depot"   # frontend greys these + depot icon
        buses.append(bus)
    return buses


def _is_first_stop(row) -> bool:
    # collector stores the matched stop's sequence; GTFS sequences start at 1
    try:
        return int(row["stop_sequence"] or 0) == 1
    except (KeyError, TypeError, ValueError):
        return False

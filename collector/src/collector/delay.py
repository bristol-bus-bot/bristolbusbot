"""Calculate live and audit delay observations from matched journeys.

Delay values are stored as integer seconds. The live estimate uses the nearest
scheduled stop. Audit observations are retained at registered timing points
and filtered to the publication distance gate during aggregation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .geo import haversine_m
from .timeparse import gtfs_seconds, scheduled_local

# Observation distance and classification thresholds.
MAX_GPS_DISTANCE_M = 1000
LOW_CONFIDENCE_DISTANCE_M = 250
TIMING_POINT_GATE_M = 150
SANITY_MIN_S = -15 * 60
SANITY_MAX_S = 90 * 60
EVENT_DELAYED_MIN_S = 4 * 60
EVENT_EARLY_MAX_S = -3 * 60

# Schedule rows everywhere in this module are tuples:
# (stop_sequence, departure_time, timepoint, stop_code, stop_lat, stop_lon)


@dataclass
class ClosestStop:
    row: tuple
    distance_m: float
    index: int


@dataclass
class LiveEstimate:
    delay_s: int
    stop_code: str | None
    stop_name: str | None
    stop_sequence: int
    distance_m: int
    low_confidence: bool
    event_type: str          # 'delayed' | 'early' | 'punctual'
    scheduled_local: datetime


@dataclass
class SettledReading:
    """One timing-point observation, audit-grade."""
    stop_sequence: int
    stop_code: str | None
    scheduled_local: datetime
    observed_delay_s: int
    on_time: bool
    gps_distance_m: int


ON_TIME_LOW_S = -60
ON_TIME_HIGH_S = 359


def closest_stop(lat: float, lon: float, schedule_rows: list) -> ClosestStop | None:
    """Nearest schedule stop to the vehicle by great-circle distance."""
    best = None
    best_dist = float("inf")
    best_i = -1
    for i, row in enumerate(schedule_rows):
        slat, slon = row[4], row[5]
        if slat is None or slon is None:
            continue
        try:
            dist = haversine_m(lat, lon, float(slat), float(slon))
        except (TypeError, ValueError):
            continue
        if dist < best_dist:
            best_dist = dist
            best = row
            best_i = i
    if best is None:
        return None
    return ClosestStop(row=best, distance_m=best_dist, index=best_i)


def classify_event(delay_s: int) -> str:
    if delay_s >= EVENT_DELAYED_MIN_S:
        return "delayed"
    if delay_s <= EVENT_EARLY_MAX_S:
        return "early"
    return "punctual"


def live_estimate(lat: float, lon: float, recorded_utc: datetime,
                  schedule_rows: list, service_midnight_dt: datetime
                  ) -> LiveEstimate | None:
    """Return a nearest-stop estimate when distance and time checks pass."""
    cs = closest_stop(lat, lon, schedule_rows)
    if cs is None or cs.distance_m > MAX_GPS_DISTANCE_M:
        return None
    seq, dep_time, _timepoint, stop_code = cs.row[0], cs.row[1], cs.row[2], cs.row[3]
    stop_name = cs.row[6] if len(cs.row) > 6 else None
    stop_secs = gtfs_seconds(dep_time)
    if stop_secs is None:
        return None
    sched = scheduled_local(service_midnight_dt, stop_secs)
    delay_s = int(round((recorded_utc - sched.astimezone(timezone.utc)).total_seconds()))
    if not (SANITY_MIN_S <= delay_s <= SANITY_MAX_S):
        return None
    return LiveEstimate(
        delay_s=delay_s,
        stop_code=stop_code,
        stop_name=stop_name,
        stop_sequence=int(seq),
        distance_m=int(cs.distance_m),
        low_confidence=cs.distance_m > LOW_CONFIDENCE_DISTANCE_M,
        event_type=classify_event(delay_s),
        scheduled_local=sched,
    )


def settled_reading(lat: float, lon: float, recorded_utc: datetime,
                    schedule_rows: list, service_midnight_dt: datetime
                    ) -> SettledReading | None:
    """Return a timing-point observation within the collection distance gate.

    The database keeps the closest reading per trip and timing point. Rollup
    applies the narrower publication distance gate.
    """
    cs = closest_stop(lat, lon, schedule_rows)
    if cs is None or cs.distance_m > MAX_GPS_DISTANCE_M:
        return None
    seq, dep_time, timepoint, stop_code = cs.row[0], cs.row[1], cs.row[2], cs.row[3]
    if int(timepoint or 0) != 1:
        return None
    stop_secs = gtfs_seconds(dep_time)
    if stop_secs is None:
        return None
    sched = scheduled_local(service_midnight_dt, stop_secs)
    delay_s = int(round((recorded_utc - sched.astimezone(timezone.utc)).total_seconds()))
    if not (SANITY_MIN_S <= delay_s <= SANITY_MAX_S):
        return None
    return SettledReading(
        stop_sequence=int(seq),
        stop_code=stop_code,
        scheduled_local=sched,
        observed_delay_s=delay_s,
        on_time=ON_TIME_LOW_S <= delay_s <= ON_TIME_HIGH_S,
        gps_distance_m=int(cs.distance_m),
    )

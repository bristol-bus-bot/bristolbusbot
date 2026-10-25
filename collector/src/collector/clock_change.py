"""Overnight journeys on clock-change nights.

On those nights an overnight journey may run to the GTFS standard (elapsed
running time) or to the printed clock time, an hour apart, and the feed
does not say which. A journey is moved to the clock reading only when every
reading so far fits it and none fits the standard; until a journey has two
readings its live delay is withheld rather than guessed.
"""
from __future__ import annotations

PENDING_READINGS = 2
NORMAL_LOW_S = -15 * 60
NORMAL_HIGH_S = 30 * 60


def _normal(delay_s: int) -> bool:
    return NORMAL_LOW_S <= delay_s <= NORMAL_HIGH_S


class ClockChangeTrips:
    """Per-journey choice between the standard and the clock reading."""

    def __init__(self) -> None:
        self._readings: dict[tuple[str, str], list[tuple[int, int]]] = {}

    def observe(self, service_date: str, trip_id: str,
                standard_delay_s: int, offset_s: int) -> str:
        """Record one reading and return 'standard', 'clock' or 'pending'."""
        if offset_s == 0:
            return self.decision(service_date, trip_id)
        # Keep the latest two service days; nothing older can still be running.
        recent = sorted({key[0] for key in self._readings} | {service_date})[-2:]
        for key in [key for key in self._readings if key[0] not in recent]:
            del self._readings[key]
        readings = self._readings.setdefault((service_date, trip_id), [])
        readings.append((standard_delay_s, offset_s))
        return self.decision(service_date, trip_id)

    def decision(self, service_date: str, trip_id: str) -> str:
        readings = self._readings.get((service_date, trip_id))
        if not readings:
            return "standard"
        if len(readings) < PENDING_READINGS:
            return "pending"
        if all(_normal(d - o) and not _normal(d) for d, o in readings):
            return "clock"
        return "standard"

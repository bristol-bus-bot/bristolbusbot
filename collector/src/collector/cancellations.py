"""Parse the dedicated BODS SIRI-SX vehicle-cancellation feed.

This is deliberately separate from :mod:`collector.sirisx`.  The normal
disruptions feed describes affected operators, lines and stops, whereas the
cancellations endpoint identifies individual scheduled vehicle journeys under
``Affects/VehicleJourneys``.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .siri import get_nested_value


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class CancellationJourney:
    """A minimal journey record; aggregate output removes its situation ID."""

    situation_number: str
    participant: str
    version: int
    versioned_at: str
    progress: str
    validity_start: str
    validity_end: str
    operator_ref: str
    vehicle_journey_ref: str
    dated_vehicle_journey_ref: str
    line_ref: str
    published_line_name: str
    origin_aimed_departure_time: str
    destination_aimed_arrival_time: str
    journey_condition: str
    route_detail_present: bool
    call_count: int
    call_conditions: dict[str, int]
    stop_ref_prefixes_4: dict[str, int]


def _validity_bounds(element: dict) -> tuple[str, str]:
    periods = [p for p in _as_list(element.get("ValidityPeriod"))
               if isinstance(p, dict)]
    starts = [_text(p.get("StartTime")) for p in periods if p.get("StartTime")]
    ends = [_text(p.get("EndTime")) for p in periods if p.get("EndTime")]
    return (min(starts, default=""), max(ends, default=""))


def _fallback_operator(affects: dict) -> str:
    operators = []
    for operator in _as_list(get_nested_value(
            affects, "Operators/AffectedOperator")):
        if isinstance(operator, dict):
            ref = _text(operator.get("OperatorRef"))
            if ref:
                operators.append(ref)
    return operators[0] if len(set(operators)) == 1 else ""


def _fallback_condition(element: dict) -> str:
    """Return the situation consequence used by current BODS publishers."""
    for consequence in _as_list(get_nested_value(
            element, "Consequences/Consequence")):
        if isinstance(consequence, dict):
            condition = _text(consequence.get("Condition"))
            if condition:
                return condition
    return ""


def parse_cancellation_journeys(parsed_xml: dict) -> list[CancellationJourney]:
    """Return journey records from an ``xmltodict`` SIRI-SX document."""
    situations = get_nested_value(
        parsed_xml, "Siri/ServiceDelivery/SituationExchangeDelivery/Situations")
    output: list[CancellationJourney] = []

    for element in _as_list((situations or {}).get("PtSituationElement")):
        if not isinstance(element, dict):
            continue
        situation_number = _text(element.get("SituationNumber"))
        if not situation_number:
            continue
        try:
            version = int(element.get("Version") or 0)
        except (TypeError, ValueError):
            version = 0
        validity_start, validity_end = _validity_bounds(element)
        affects = element.get("Affects") or {}
        fallback_operator = _fallback_operator(affects)
        fallback_condition = _fallback_condition(element)
        journeys = _as_list(get_nested_value(
            affects, "VehicleJourneys/AffectedVehicleJourney"))

        for journey in journeys:
            if not isinstance(journey, dict):
                continue
            calls = [call for call in _as_list(
                get_nested_value(journey, "Calls/Call")) if isinstance(call, dict)]
            call_conditions = Counter(
                _text(call.get("CallCondition")) or "missing" for call in calls)
            stop_prefixes = Counter(
                (_text(call.get("StopPointRef"))[:4] or "missing")
                for call in calls
            )
            route = journey.get("Route")
            route_detail_present = isinstance(route, dict) and bool(route)
            output.append(CancellationJourney(
                situation_number=situation_number,
                participant=_text(element.get("ParticipantRef")),
                version=version,
                versioned_at=_text(element.get("VersionedAtTime")),
                progress=_text(element.get("Progress")),
                validity_start=validity_start,
                validity_end=validity_end,
                operator_ref=_text(get_nested_value(
                    journey, "Operator/OperatorRef")) or fallback_operator,
                vehicle_journey_ref=_text(journey.get("VehicleJourneyRef")),
                dated_vehicle_journey_ref=_text(
                    journey.get("DatedVehicleJourneyRef")),
                line_ref=_text(journey.get("LineRef")),
                published_line_name=_text(journey.get("PublishedLineName")),
                origin_aimed_departure_time=_text(
                    journey.get("OriginAimedDepartureTime")),
                destination_aimed_arrival_time=_text(
                    journey.get("DestinationAimedArrivalTime")),
                journey_condition=(
                    _text(journey.get("JourneyCondition")) or fallback_condition
                ),
                route_detail_present=route_detail_present,
                call_count=len(calls),
                call_conditions=dict(sorted(call_conditions.items())),
                stop_ref_prefixes_4=dict(sorted(stop_prefixes.items())),
            ))

    return output


FIELD_CHECKS = {
    "vehicle_journey_ref": lambda row: bool(row.vehicle_journey_ref),
    "dated_vehicle_journey_ref": lambda row: bool(row.dated_vehicle_journey_ref),
    "line_ref": lambda row: bool(row.line_ref),
    "published_line_name": lambda row: bool(row.published_line_name),
    "origin_aimed_departure_time": lambda row: bool(
        row.origin_aimed_departure_time),
    "destination_aimed_arrival_time": lambda row: bool(
        row.destination_aimed_arrival_time),
    "route_detail": lambda row: row.route_detail_present,
    "calls": lambda row: row.call_count > 0,
}


def _operator_summary(rows: list[CancellationJourney]) -> dict:
    versions = Counter(row.version for row in rows)
    stop_prefixes = Counter()
    for row in rows:
        stop_prefixes.update(row.stop_ref_prefixes_4)
    return {
        "situations": len({row.situation_number for row in rows}),
        "journeys": len(rows),
        "conditions": dict(sorted(Counter(
            row.journey_condition or "missing" for row in rows).items())),
        "progress": dict(sorted(Counter(
            row.progress or "missing" for row in rows).items())),
        "versions": {str(key): value for key, value in sorted(versions.items())},
        "latest_version_gt_1": sum(row.version > 1 for row in rows),
        "field_presence": {
            name: sum(bool(check(row)) for row in rows)
            for name, check in FIELD_CHECKS.items()
        },
        "origin_date_range": [
            min((row.origin_aimed_departure_time[:10] for row in rows
                 if row.origin_aimed_departure_time), default=""),
            max((row.origin_aimed_departure_time[:10] for row in rows
                 if row.origin_aimed_departure_time), default=""),
        ],
        "versioned_at_present": sum(bool(row.versioned_at) for row in rows),
        "validity_end_present": sum(bool(row.validity_end) for row in rows),
        "stop_ref_prefixes_4": dict(sorted(stop_prefixes.items())),
    }


def _geography_summary(
        rows: list[CancellationJourney], target_stop_prefixes: list[str]) -> dict:
    prefixes = sorted({prefix.strip() for prefix in target_stop_prefixes
                       if prefix.strip()})
    matching_rows = [
        row for row in rows
        if any(prefix in row.stop_ref_prefixes_4 for prefix in prefixes)
    ]
    matching_calls = Counter()
    for row in matching_rows:
        matching_calls.update({
            prefix: count
            for prefix, count in row.stop_ref_prefixes_4.items()
            if prefix in prefixes
        })
    return {
        "requested_stop_ref_prefixes_4": prefixes,
        "journeys_touching_requested_prefixes": len(matching_rows),
        "publishing_operators": dict(sorted(Counter(
            row.operator_ref or "UNKNOWN" for row in matching_rows).items())),
        "conditions": dict(sorted(Counter(
            row.journey_condition or "missing"
            for row in matching_rows).items())),
        "progress": dict(sorted(Counter(
            row.progress or "missing" for row in matching_rows).items())),
        "matching_call_prefixes_4": dict(sorted(matching_calls.items())),
        "journeys_without_calls": sum(row.call_count == 0 for row in rows),
    }


def summarise_cancellations(
        rows: list[CancellationJourney], target_operators: list[str],
        target_stop_prefixes: list[str] | None = None) -> dict:
    """Build a safe aggregate; no situation or journey identifiers are emitted."""
    by_operator: dict[str, list[CancellationJourney]] = {}
    for row in rows:
        key = row.operator_ref or "UNKNOWN"
        by_operator.setdefault(key, []).append(row)

    targets = {
        operator: _operator_summary(by_operator.get(operator, []))
        for operator in target_operators
    }
    summary = {
        "situations": len({row.situation_number for row in rows}),
        "journeys": len(rows),
        "publishing_operators": {
            operator: len(operator_rows)
            for operator, operator_rows in sorted(by_operator.items())
        },
        "feed_progress": dict(sorted(Counter(
            row.progress or "missing" for row in rows).items())),
        "target_operators": targets,
    }
    if target_stop_prefixes is not None:
        summary["target_geography"] = _geography_summary(
            rows, target_stop_prefixes)
    return summary

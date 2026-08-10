#!/usr/bin/env python3
"""Artifact-specific validation and bounded comparison for enrichment data."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Mapping


MINIMUM_RATIO = 0.80
MAXIMUM_RATIO = 1.25
MAX_FLEET_RECORDS = 50_000
MAX_LOCALITY_RECORDS = 100_000
OPERATOR_ID_RE = re.compile(r"[A-Z0-9]{2,8}")
WECA_AREAS = {
    "Bath and North East Somerset",
    "Bristol",
    "North Somerset",
    "South Gloucestershire",
    "Unknown",
}
AREA_ALIASES = {
    "Bath": "Bath and North East Somerset",
}
FLEET_OPERATOR_TRANSITIONS = {
    # Westlink's bustimes records moved from the legacy VITR operator id to
    # KEMT.  Count this as a rename only when every exact live vehicle id is
    # still present under KEMT; otherwise the normal collapse guard rejects it.
    "VITR": "KEMT",
}


class EnrichmentContractError(ValueError):
    """An enrichment artifact violates its code-owned data contract."""


def _json(raw: bytes, label: str) -> object:
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnrichmentContractError(f"{label} is invalid JSON") from exc


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise EnrichmentContractError(f"{label} must be text")
    cleaned = value.strip()
    if not cleaned and not allow_empty:
        raise EnrichmentContractError(f"{label} cannot be empty")
    return cleaned


def validate_fleet(raw: bytes) -> dict[str, object]:
    value = _json(raw, "fleet candidate")
    if not isinstance(value, list) or not value \
            or len(value) > MAX_FLEET_RECORDS:
        raise EnrichmentContractError(
            "fleet candidate must be a bounded non-empty list")

    record_ids: set[int] = set()
    slugs: set[str] = set()
    operator_counts: Counter[str] = Counter()
    active_operator_counts: Counter[str] = Counter()
    active_operator_record_ids: dict[str, list[int]] = {}
    unidentified_records = 0
    for index, record in enumerate(value):
        label = f"fleet record {index}"
        if not isinstance(record, dict):
            raise EnrichmentContractError(f"{label} must be an object")
        record_id = record.get("id")
        if not isinstance(record_id, int) or isinstance(record_id, bool) \
                or record_id <= 0 or record_id in record_ids:
            raise EnrichmentContractError(
                f"{label} has an invalid or duplicate id")
        record_ids.add(record_id)
        slug = _text(record.get("slug"), f"{label} slug")
        if slug in slugs:
            raise EnrichmentContractError(f"{label} has a duplicate slug")
        slugs.add(slug)

        operator = record.get("operator")
        if not isinstance(operator, dict):
            raise EnrichmentContractError(f"{label} operator must be an object")
        operator_id = _text(
            operator.get("id"), f"{label} operator id").upper()
        if not OPERATOR_ID_RE.fullmatch(operator_id):
            raise EnrichmentContractError(f"{label} operator id is invalid")
        _text(operator.get("slug"), f"{label} operator slug")
        _text(operator.get("name"), f"{label} operator name")

        fleet_code = _text(
            record.get("fleet_code"), f"{label} fleet code", allow_empty=True)
        registration = _text(
            record.get("reg"), f"{label} registration", allow_empty=True)
        fleet_number = record.get("fleet_number")
        if fleet_number is not None and (
                not isinstance(fleet_number, int)
                or isinstance(fleet_number, bool) or fleet_number < 0):
            raise EnrichmentContractError(f"{label} fleet number is invalid")
        if not fleet_code and fleet_number is None and not registration:
            unidentified_records += 1
        withdrawn = record.get("withdrawn")
        if not isinstance(withdrawn, bool):
            raise EnrichmentContractError(f"{label} withdrawn must be boolean")
        for field in ("livery", "vehicle_type", "garage"):
            if record.get(field) is not None \
                    and not isinstance(record.get(field), dict):
                raise EnrichmentContractError(
                    f"{label} {field} must be an object or null")
        features = record.get("special_features")
        if features is not None and not isinstance(features, list):
            raise EnrichmentContractError(
                f"{label} special_features must be a list or null")

        operator_counts[operator_id] += 1
        if not withdrawn:
            active_operator_counts[operator_id] += 1
            active_operator_record_ids.setdefault(
                operator_id, []).append(record_id)

    return {
        "policy": "fleet-structure-v1",
        "records": len(value),
        "active_records": sum(active_operator_counts.values()),
        "unidentified_records": unidentified_records,
        "operator_counts": dict(sorted(operator_counts.items())),
        "active_operator_counts": dict(sorted(active_operator_counts.items())),
        "active_operator_record_ids": {
            operator: sorted(ids)
            for operator, ids in sorted(active_operator_record_ids.items())
        },
    }


def validate_localities(raw: bytes) -> dict[str, object]:
    value = _json(raw, "locality candidate")
    if not isinstance(value, dict) or not value \
            or len(value) > MAX_LOCALITY_RECORDS:
        raise EnrichmentContractError(
            "locality candidate must be a bounded non-empty object")

    area_counts: Counter[str] = Counter()
    for key, record in value.items():
        if not isinstance(key, str) or not key.strip() or len(key) > 64:
            raise EnrichmentContractError("locality key is invalid")
        label = f"locality {key}"
        if not isinstance(record, dict):
            raise EnrichmentContractError(f"{label} must be an object")
        if _text(record.get("stop_code"), f"{label} stop code") != key:
            raise EnrichmentContractError(
                f"{label} key does not match its stop code")
        _text(record.get("stop_name"), f"{label} stop name")
        area = _text(record.get("area"), f"{label} area")
        canonical_area = AREA_ALIASES.get(area, area)
        if canonical_area not in WECA_AREAS:
            raise EnrichmentContractError(f"{label} area is outside the contract")
        for field in ("ward_name", "ward_code"):
            item = record.get(field)
            if item is not None and not isinstance(item, str):
                raise EnrichmentContractError(
                    f"{label} {field} must be text or null")
        latitude = record.get("lat")
        longitude = record.get("lon")
        if isinstance(latitude, bool) or not isinstance(latitude, (int, float)) \
                or not 49 <= latitude <= 61:
            raise EnrichmentContractError(f"{label} latitude is invalid")
        if isinstance(longitude, bool) \
                or not isinstance(longitude, (int, float)) \
                or not -8 <= longitude <= 2:
            raise EnrichmentContractError(f"{label} longitude is invalid")
        area_counts[canonical_area] += 1

    return {
        "policy": "locality-structure-v1",
        "records": len(value),
        "area_counts": dict(sorted(area_counts.items())),
    }


def _counts(summary: Mapping[str, object], field: str) -> dict[str, int]:
    value = summary.get(field)
    if not isinstance(value, dict) or not all(
            isinstance(key, str)
            and isinstance(count, int) and not isinstance(count, bool)
            and count >= 0 for key, count in value.items()):
        raise EnrichmentContractError(f"{field} summary is invalid")
    return value


def _record_ids(summary: Mapping[str, object], field: str) \
        -> dict[str, set[int]]:
    value = summary.get(field)
    if not isinstance(value, dict):
        raise EnrichmentContractError(f"{field} summary is invalid")
    result: dict[str, set[int]] = {}
    for operator, ids in value.items():
        if not isinstance(operator, str) or not isinstance(ids, list) \
                or not all(isinstance(record_id, int)
                           and not isinstance(record_id, bool)
                           and record_id > 0 for record_id in ids) \
                or len(ids) != len(set(ids)):
            raise EnrichmentContractError(f"{field} summary is invalid")
        result[operator] = set(ids)
    return result


def _bounded(candidate: int, live: int, label: str) -> dict[str, int]:
    minimum = max(1, math.ceil(live * MINIMUM_RATIO))
    maximum = max(math.ceil(live * MAXIMUM_RATIO), live + 5)
    if candidate < minimum:
        raise EnrichmentContractError(
            f"{label} collapsed from {live} to {candidate}; minimum is {minimum}")
    if candidate > maximum:
        raise EnrichmentContractError(
            f"{label} jumped from {live} to {candidate}; maximum is {maximum}")
    return {"live": live, "candidate": candidate,
            "minimum": minimum, "maximum": maximum}


def _not_collapsed(candidate: int, live: int, label: str) -> dict[str, int]:
    minimum = max(1, math.ceil(live * MINIMUM_RATIO))
    if candidate < minimum:
        raise EnrichmentContractError(
            f"{label} collapsed from {live} to {candidate}; minimum is {minimum}")
    return {"live": live, "candidate": candidate, "minimum": minimum}


def compare_fleet(candidate: Mapping[str, object],
                  live: Mapping[str, object]) -> dict[str, object]:
    candidate_counts = dict(_counts(candidate, "active_operator_counts"))
    live_counts = dict(_counts(live, "active_operator_counts"))
    candidate_ids = _record_ids(candidate, "active_operator_record_ids")
    live_ids = _record_ids(live, "active_operator_record_ids")
    transitions: list[dict[str, object]] = []
    for legacy, replacement in FLEET_OPERATOR_TRANSITIONS.items():
        legacy_ids = live_ids.get(legacy, set())
        if not legacy_ids:
            continue
        remaining_legacy_ids = candidate_ids.get(legacy, set())
        if remaining_legacy_ids:
            transitions.append({
                "legacy": legacy,
                "replacement": replacement,
                "status": "source-still-uses-legacy-id",
                "live_legacy_records": len(legacy_ids),
                "candidate_legacy_records": len(remaining_legacy_ids),
            })
            continue
        missing = sorted(legacy_ids - candidate_ids.get(replacement, set()))
        if missing:
            raise EnrichmentContractError(
                f"operator transition {legacy}->{replacement} is incomplete; "
                f"{len(missing)} live vehicle ids are missing")
        moved = live_counts.pop(legacy, 0)
        live_counts[replacement] = live_counts.get(replacement, 0) + moved
        transitions.append({
            "legacy": legacy,
            "replacement": replacement,
            "status": "exact-id-transition-accepted",
            "live_legacy_records": len(legacy_ids),
            "matched_replacement_records": len(legacy_ids),
            "missing_ids": 0,
        })
    records = _bounded(
        int(candidate.get("records", -1)), int(live.get("records", -1)),
        "fleet total")
    totals = _bounded(
        int(candidate.get("active_records", -1)),
        int(live.get("active_records", -1)),
        "active fleet total")
    operators = {
        operator: _not_collapsed(
            candidate_counts.get(operator, 0), count, f"operator {operator}")
        for operator, count in live_counts.items()
    }
    live_unidentified = int(live.get("unidentified_records", -1))
    candidate_unidentified = int(candidate.get("unidentified_records", -1))
    maximum_unidentified = max(
        math.ceil(live_unidentified * MAXIMUM_RATIO), live_unidentified + 5)
    if candidate_unidentified < 0 \
            or candidate_unidentified > maximum_unidentified:
        raise EnrichmentContractError(
            "fleet records without a public identity jumped from "
            f"{live_unidentified} to {candidate_unidentified}; maximum is "
            f"{maximum_unidentified}")
    return {
        "policy": "fleet-bounded-count-v2",
        "records": records,
        "totals": totals,
        "operators": operators,
        "new_operators": sorted(set(candidate_counts) - set(live_counts)),
        "operator_transitions": transitions,
        "unidentified_records": {
            "live": live_unidentified,
            "candidate": candidate_unidentified,
            "maximum": maximum_unidentified,
        },
    }


def compare_localities(candidate: Mapping[str, object],
                       live: Mapping[str, object]) -> dict[str, object]:
    totals = _bounded(
        int(candidate.get("records", -1)), int(live.get("records", -1)),
        "locality total")
    candidate_counts = _counts(candidate, "area_counts")
    live_counts = _counts(live, "area_counts")
    areas = {
        area: _not_collapsed(
            candidate_counts.get(area, 0), count, f"area {area}")
        for area, count in live_counts.items() if area != "Unknown"
    }
    return {
        "policy": "locality-bounded-count-v1",
        "totals": totals,
        "areas": areas,
        "unknown": {
            "live": live_counts.get("Unknown", 0),
            "candidate": candidate_counts.get("Unknown", 0),
        },
    }

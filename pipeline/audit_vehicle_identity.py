#!/usr/bin/env python3
"""Read-only audit for cross-operator fleet identity collisions.

The live site historically indexed fleet data by a bare fleet code. Different
operators can legitimately reuse the same code, so the last record loaded could
provide another operator's model or livery. This command compares that legacy
lookup with an operator-scoped lookup for vehicles actually observed by the
collector. It never writes to the databases or enrichment files and never
prints description text.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable


DEFAULT_FLEET = Path("/var/lib/bristolbusbot/enrichment/fbribuses.json")
DEFAULT_LIVE_DB = Path("/var/lib/bristolbusbot/collector/live.db")
DEFAULT_AUDIT_DB = Path("/var/lib/bristolbusbot/collector/audit.db")
DEFAULT_DESCRIPTIONS = (
    Path("/var/lib/bristolbusbot/enrichment/bus-descriptions.json"),
    Path("/var/lib/bristolbusbot/enrichment/waiting-descriptions.json"),
    Path("/var/lib/bristolbusbot/enrichment/depot-descriptions.json"),
)
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def operator_id(record: dict) -> str:
    operator = record.get("operator") or {}
    value = operator.get("id") if isinstance(operator, dict) else operator
    return str(value or "").strip().upper()


def fleet_code(record: dict) -> str:
    return str(record.get("fleet_code")
               or record.get("fleet_number") or "").strip()


def normalise_registration(value: object) -> str:
    return _NON_ALNUM.sub("", str(value or "").strip().upper())


def identity(record: dict) -> tuple[str, str, str]:
    return (operator_id(record), fleet_code(record),
            normalise_registration(record.get("reg")))


def possible_fleet_codes(vehicle_ref: object) -> list[str]:
    raw = str(vehicle_ref or "").strip()
    if not raw:
        return []
    candidates = [raw]
    if "-" in raw:
        candidates.append(raw.rsplit("-", 1)[-1])
    return list(dict.fromkeys(value for value in candidates if value))


def load_fleet(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"fleet data could not be read: {path.name}") from exc
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"fleet data is empty or invalid: {path.name}")
    return [item for item in payload if isinstance(item, dict)]


def load_description_keys(paths: Iterable[Path]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for path in paths:
        if not path.is_file():
            result[path.name] = set()
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"description data could not be read: {path.name}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(
                f"description data is not an object: {path.name}")
        result[path.name] = {str(key) for key in payload}
    return result


def _connect_read_only(path: Path) -> sqlite3.Connection:
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError(f"database could not be opened read-only: {path.name}") from exc
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def observed_identities(live_db: Path, audit_db: Path,
                        observed_days: int = 56) -> set[tuple[str, str]]:
    observed: set[tuple[str, str]] = set()
    with _connect_read_only(live_db) as connection:
        observed.update(
            (str(operator or "").strip().upper(), str(vehicle or "").strip())
            for operator, vehicle in connection.execute(
                "SELECT DISTINCT operator_ref, vehicle_ref FROM vehicles "
                "WHERE operator_ref IS NOT NULL AND vehicle_ref IS NOT NULL")
            if str(operator or "").strip() and str(vehicle or "").strip()
        )
    cutoff = (date.today() - timedelta(days=observed_days)).isoformat()
    with _connect_read_only(audit_db) as connection:
        observed.update(
            (str(operator or "").strip().upper(), str(vehicle or "").strip())
            for operator, vehicle in connection.execute(
                "SELECT DISTINCT operator, vehicle_ref "
                "FROM timepoint_observations "
                "WHERE service_date >= ? AND operator IS NOT NULL "
                "AND vehicle_ref IS NOT NULL", (cutoff,))
            if str(operator or "").strip() and str(vehicle or "").strip()
        )
    return observed


def indexes(records: list[dict]) -> dict:
    registrations: dict[str, dict] = {}
    registrations_scoped: dict[tuple[str, str], dict] = {}
    legacy_codes: dict[str, dict] = {}
    scoped_groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    registration_groups: dict[str, list[dict]] = defaultdict(list)
    active_code_operators: dict[str, set[str]] = defaultdict(set)

    for record in records:
        operator = operator_id(record)
        code = fleet_code(record)
        registration = normalise_registration(record.get("reg"))
        if operator and code:
            scoped_groups[(operator, code)].append(record)
            if not record.get("withdrawn"):
                active_code_operators[code].add(operator)
        if code:
            # Deliberately reproduce the site's historical last-record-wins
            # bare-code index so the audit measures the real failure mode.
            legacy_codes[code] = record
        if registration:
            registrations[registration] = record
            if operator:
                registrations_scoped[(operator, registration)] = record
            registration_groups[registration].append(record)

    return {
        "registrations": registrations,
        "registrations_scoped": registrations_scoped,
        "legacy_codes": legacy_codes,
        "scoped_groups": scoped_groups,
        "registration_groups": registration_groups,
        "active_code_operators": active_code_operators,
    }


def preferred_scoped_record(records: list[dict]) -> dict | None:
    """Return one canonical record, refusing an ambiguous reused fleet code."""
    active = [record for record in records if not record.get("withdrawn")]
    candidates = active or records
    registrations = {
        normalise_registration(record.get("reg")) for record in candidates
        if normalise_registration(record.get("reg"))
    }
    if len(candidates) == 1 or len(registrations) == 1:
        return candidates[-1]
    return None


def safe_match(index: dict, operator: str, vehicle_ref: str) -> dict | None:
    registration = normalise_registration(vehicle_ref)
    direct = index["registrations_scoped"].get((operator, registration))
    if direct:
        return direct
    for code in possible_fleet_codes(vehicle_ref):
        record = preferred_scoped_record(
            index["scoped_groups"].get((operator, code), []))
        if record:
            return record
    # A bare-code fallback is safe only when exactly one active operator owns
    # that code. This preserves compatibility without cross-wiring collisions.
    for code in possible_fleet_codes(vehicle_ref):
        owners = index["active_code_operators"].get(code, set())
        if len(owners) == 1:
            owner = next(iter(owners))
            if operator and owner != operator:
                continue
            return preferred_scoped_record(
                index["scoped_groups"].get((owner, code), []))
    return None


def legacy_match(index: dict, vehicle_ref: str) -> dict | None:
    raw = str(vehicle_ref or "").strip()
    if not raw:
        return None
    code = raw.rsplit("-", 1)[-1]
    record = index["legacy_codes"].get(code)
    if record:
        return record
    return index["registrations"].get(normalise_registration(raw))


def build_report(records: list[dict], observed: set[tuple[str, str]],
                 description_keys: dict[str, set[str]],
                 max_examples: int = 20) -> dict:
    index = indexes(records)
    shared_codes = {
        code: owners for code, owners in index["active_code_operators"].items()
        if len(owners) > 1
    }
    scoped_duplicates = {
        key: group for key, group in index["scoped_groups"].items()
        if len(group) > 1
    }
    registration_duplicates = {
        key: group for key, group in index["registration_groups"].items()
        if len({identity(record) for record in group}) > 1
    }

    wrong: list[dict] = []
    unresolved: list[dict] = []
    ambiguous_descriptions: set[tuple[str, str, str]] = set()
    safe_matches = legacy_matches = 0
    shared_description_codes = set().union(*description_keys.values()) \
        & set(shared_codes)

    for operator, vehicle_ref in sorted(observed):
        safe = safe_match(index, operator, vehicle_ref)
        legacy = legacy_match(index, vehicle_ref)
        safe_matches += int(safe is not None)
        legacy_matches += int(legacy is not None)
        codes = possible_fleet_codes(vehicle_ref)
        code = next((item for item in codes
                     if (operator, item) in index["scoped_groups"]),
                    codes[-1] if codes else "")
        if code in shared_description_codes:
            ambiguous_descriptions.add((operator, vehicle_ref, code))
        if safe is None:
            unresolved.append({
                "operator": operator,
                "vehicle_ref": vehicle_ref,
                "candidate_fleet_code": code,
                "legacy_operator": operator_id(legacy) if legacy else None,
            })
            continue
        if legacy is not None and identity(legacy) != identity(safe):
            wrong.append({
                "operator": operator,
                "vehicle_ref": vehicle_ref,
                "fleet_code": fleet_code(safe),
                "correct_operator": operator_id(safe),
                "legacy_operator": operator_id(legacy),
                "correct_registration": normalise_registration(safe.get("reg")),
                "legacy_registration": normalise_registration(legacy.get("reg")),
            })

    ambiguous_by_file = {
        name: sorted(keys & set(shared_codes))
        for name, keys in sorted(description_keys.items())
    }
    active_records = [record for record in records if not record.get("withdrawn")]
    return {
        "schema": 1,
        "mode": "read_only",
        "status": "issues_found" if wrong or ambiguous_descriptions else "ok",
        "fleet": {
            "records": len(records),
            "active_records": len(active_records),
            "operators": len({operator_id(record) for record in active_records
                              if operator_id(record)}),
            "shared_active_fleet_code_groups": len(shared_codes),
            "same_operator_duplicate_groups": len(scoped_duplicates),
            "registration_collision_groups": len(registration_duplicates),
        },
        "observed": {
            "identities": len(observed),
            "operator_safe_matches": safe_matches,
            "legacy_matches": legacy_matches,
            "wrong_legacy_fleet_matches": len(wrong),
            "unresolved_operator_safe_matches": len(unresolved),
            "ambiguous_description_identities": len(ambiguous_descriptions),
        },
        "descriptions": {
            name: {
                "keys": len(description_keys[name]),
                "shared_fleet_code_keys": len(ambiguous_by_file[name]),
            }
            for name in sorted(description_keys)
        },
        "examples": {
            "wrong_legacy_fleet_matches": wrong[:max_examples],
            "unresolved_operator_safe_matches": unresolved[:max_examples],
            "ambiguous_description_identities": [
                {"operator": operator, "vehicle_ref": vehicle,
                 "fleet_code": code}
                for operator, vehicle, code in
                sorted(ambiguous_descriptions)[:max_examples]
            ],
            "shared_active_fleet_codes": [
                {"fleet_code": code, "operators": sorted(owners)}
                for code, owners in sorted(shared_codes.items())[:max_examples]
            ],
            "same_operator_duplicate_groups": [
                {
                    "operator": key[0],
                    "fleet_code": key[1],
                    "registrations": sorted({
                        normalise_registration(record.get("reg"))
                        for record in group
                        if normalise_registration(record.get("reg"))
                    }),
                }
                for key, group in
                sorted(scoped_duplicates.items())[:max_examples]
            ],
            "registration_collision_groups": [
                {
                    "registration": registration,
                    "operator_fleet_codes": [
                        {"operator": operator_id(record),
                         "fleet_code": fleet_code(record)}
                        for record in group
                    ],
                }
                for registration, group in
                sorted(registration_duplicates.items())[:max_examples]
            ],
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fleet", type=Path, default=DEFAULT_FLEET)
    parser.add_argument("--live-db", type=Path, default=DEFAULT_LIVE_DB)
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    parser.add_argument("--descriptions", type=Path, action="append",
                        default=None)
    parser.add_argument("--observed-days", type=int, default=56)
    parser.add_argument("--max-examples", type=int, default=20)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 1 <= args.observed_days <= 366:
        raise SystemExit("--observed-days must be between 1 and 366")
    if not 0 <= args.max_examples <= 100:
        raise SystemExit("--max-examples must be between 0 and 100")
    description_paths = tuple(args.descriptions or DEFAULT_DESCRIPTIONS)
    report = build_report(
        load_fleet(args.fleet),
        observed_identities(args.live_db, args.audit_db, args.observed_days),
        load_description_keys(description_paths),
        args.max_examples,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

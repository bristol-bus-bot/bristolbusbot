#!/usr/bin/env python3
"""Write a bounded, read-only report about enrichment completeness.

This job deliberately has no promotion or repair code.  Findings are written
for the aggregate health snapshot and digest; they never alter source data.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEMA_VERSION = 1
THRESHOLDS = {
    "observed_days": 56,
    "fleet_max_age_days": 14,
    "operator_collapse_ratio": 0.65,
    "operator_collapse_min_previous": 5,
    "max_examples": 12,
}
DEFAULT_OUTPUT = Path("/var/lib/bristolbusbot/monitoring/data-health.json")
DEFAULT_LIVE_DB = Path("/var/lib/bristolbusbot/collector/live.db")
DEFAULT_AUDIT_DB = Path("/var/lib/bristolbusbot/collector/audit.db")
DEFAULT_TIMETABLE_DB = Path("/var/lib/bristolbusbot/pipeline/timetable.db")
DEFAULT_FLEET = Path("/var/lib/bristolbusbot/enrichment/fbribuses.json")
DEFAULT_LOCALITIES = Path(
    "/var/lib/bristolbusbot/enrichment/stop_localities.json")
DEFAULT_MODEL_CONTEXT = Path(
    "/var/lib/bristolbusbot/enrichment/model-context.json")
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_WHITE_LIVERIES = {"white", "#fff", "#ffffff", "rgb(255,255,255)"}
# Must remain aligned with geocode_stops.py and the public stop-search scope.
WECA_BBOX = (51.2731, 51.6773, -3.1151, -2.2521)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o640)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def load_json(path: Path, expected: type) -> object:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {path.name}") from exc
    if not isinstance(payload, expected):
        raise RuntimeError(f"{path.name} has the wrong JSON shape")
    return payload


def connect_read_only(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    except (OSError, sqlite3.Error) as exc:
        raise RuntimeError(f"could not open {path.name} read-only") from exc
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def operator_id(record: dict) -> str:
    operator = record.get("operator") or {}
    value = operator.get("id") if isinstance(operator, dict) else operator
    return str(value or "").strip().upper()


def fleet_code(record: dict) -> str:
    return str(record.get("fleet_code")
               or record.get("fleet_number") or "").strip()


def registration(value: object) -> str:
    return _NON_ALNUM.sub("", str(value or "").strip().upper())


def candidate_codes(value: object) -> list[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    candidates = [raw]
    if "-" in raw:
        candidates.append(raw.rsplit("-", 1)[-1])
    return list(dict.fromkeys(candidates))


def build_fleet_index(records: list[dict]) -> dict:
    codes: dict[tuple[str, str], list[dict]] = defaultdict(list)
    registrations: dict[tuple[str, str], list[dict]] = defaultdict(list)
    owners: dict[str, set[str]] = defaultdict(set)
    for record in records:
        if record.get("withdrawn"):
            continue
        operator = operator_id(record)
        code = fleet_code(record)
        reg = registration(record.get("reg"))
        if operator and code:
            codes[(operator, code)].append(record)
            owners[code].add(operator)
        if operator and reg:
            registrations[(operator, reg)].append(record)
    return {"codes": codes, "registrations": registrations, "owners": owners}


def unambiguous(records: list[dict]) -> dict | None:
    identities = {
        (operator_id(record), fleet_code(record),
         registration(record.get("reg")))
        for record in records
    }
    return records[-1] if records and len(identities) == 1 else None


def match_vehicle(index: dict, operator: str,
                  vehicle_ref: str) -> dict | None:
    reg = registration(vehicle_ref)
    direct = unambiguous(index["registrations"].get((operator, reg), []))
    if direct:
        return direct
    for code in candidate_codes(vehicle_ref):
        direct = unambiguous(index["codes"].get((operator, code), []))
        if direct:
            return direct
    for code in candidate_codes(vehicle_ref):
        owners = index["owners"].get(code, set())
        if len(owners) == 1:
            owner = next(iter(owners))
            if operator and operator != owner:
                continue
            direct = unambiguous(index["codes"].get((owner, code), []))
            if direct:
                return direct
    return None


def load_observed(live_db: Path, audit_db: Path,
                  days: int) -> set[tuple[str, str]]:
    observed: set[tuple[str, str]] = set()
    cutoff_time = (utcnow() - timedelta(days=days)).isoformat()
    cutoff_date = (utcnow().date() - timedelta(days=days)).isoformat()
    with connect_read_only(live_db) as connection:
        rows = connection.execute(
            "SELECT DISTINCT operator_ref, vehicle_ref FROM vehicles "
            "WHERE updated_at >= ? AND operator_ref IS NOT NULL "
            "AND vehicle_ref IS NOT NULL", (cutoff_time,))
        observed.update(
            (str(operator).strip().upper(), str(vehicle).strip())
            for operator, vehicle in rows
            if str(operator or "").strip() and str(vehicle or "").strip())
    with connect_read_only(audit_db) as connection:
        rows = connection.execute(
            "SELECT DISTINCT operator, vehicle_ref FROM timepoint_observations "
            "WHERE service_date >= ? AND operator IS NOT NULL "
            "AND vehicle_ref IS NOT NULL", (cutoff_date,))
        observed.update(
            (str(operator).strip().upper(), str(vehicle).strip())
            for operator, vehicle in rows
            if str(operator or "").strip() and str(vehicle or "").strip())
    return observed


def has_livery(record: dict) -> bool:
    livery = record.get("livery") or {}
    if not isinstance(livery, dict):
        return False
    left = str(livery.get("left") or "").strip()
    right = str(livery.get("right") or "").strip()
    return bool(left and right
                and left.lower() not in _WHITE_LIVERIES
                and right.lower() not in _WHITE_LIVERIES)


def populated_keys(path: Path) -> set[str]:
    payload = load_json(path, dict)
    return {
        str(key) for key, value in payload.items()
        if isinstance(value, str) and value.strip()
    }


def timetable_stop_codes(path: Path) -> set[str]:
    with connect_read_only(path) as connection:
        rows = connection.execute(
            "SELECT DISTINCT stop_code FROM stops "
            "WHERE stop_code IS NOT NULL AND TRIM(stop_code) <> '' "
            "AND stop_lat BETWEEN ? AND ? AND stop_lon BETWEEN ? AND ?",
            WECA_BBOX)
        return {str(row[0]).strip() for row in rows}


def previous_operator_counts(path: Path) -> dict[str, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != SCHEMA_VERSION:
            return {}
        counts = payload.get("fleet", {}).get("active_by_operator", {})
        if not isinstance(counts, dict):
            return {}
        return {
            str(key): int(value) for key, value in counts.items()
            if isinstance(value, int) and value >= 0
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def build_report(*, output: Path, live_db: Path, audit_db: Path,
                 timetable_db: Path, fleet_path: Path,
                 localities_path: Path, description_paths: dict[str, Path],
                 model_context_path: Path) -> dict:
    generated = utcnow()
    old_operator_counts = previous_operator_counts(output)
    raw_fleet = load_json(fleet_path, list)
    records = [item for item in raw_fleet if isinstance(item, dict)]
    active_records = [record for record in records if not record.get("withdrawn")]
    index = build_fleet_index(active_records)
    observed = load_observed(
        live_db, audit_db, int(THRESHOLDS["observed_days"]))
    description_keys = {
        name: populated_keys(path) for name, path in description_paths.items()
    }

    matched: dict[tuple[str, str], dict] = {}
    missing_fleet: list[str] = []
    for operator, vehicle in sorted(observed):
        record = match_vehicle(index, operator, vehicle)
        if record is None:
            missing_fleet.append(f"{operator}:{vehicle}")
        else:
            matched[(operator, vehicle)] = record

    missing_livery = sorted(
        f"{operator}:{vehicle}" for (operator, vehicle), record in matched.items()
        if not has_livery(record))
    missing_blurbs: dict[str, list[str]] = {}
    for name, keys in description_keys.items():
        missing_blurbs[name] = sorted(
            f"{operator}:{fleet_code(record)}"
            for (operator, _vehicle), record in matched.items()
            if fleet_code(record) and not (
                f"{operator}:{fleet_code(record)}" in keys
                or (fleet_code(record) in keys
                    and len(index["owners"].get(fleet_code(record), set())) == 1)
            ))

    active_by_operator = dict(sorted(Counter(
        operator_id(record) for record in active_records
        if operator_id(record)).items()))
    observed_by_operator = dict(sorted(Counter(
        operator for operator, _vehicle in observed).items()))
    collapse_ratio = float(THRESHOLDS["operator_collapse_ratio"])
    collapse_minimum = int(THRESHOLDS["operator_collapse_min_previous"])
    collapses = []
    for operator, previous in sorted(old_operator_counts.items()):
        current = active_by_operator.get(operator, 0)
        if previous >= collapse_minimum and current < previous * collapse_ratio:
            collapses.append({
                "operator": operator,
                "previous": previous,
                "current": current,
                "ratio": round(current / previous, 3),
            })

    localities = load_json(localities_path, dict)
    locality_keys = {str(key).strip() for key in localities if str(key).strip()}
    stop_codes = timetable_stop_codes(timetable_db)
    missing_localities = sorted(stop_codes - locality_keys)

    fleet_age_days = max(
        0.0, (generated.timestamp() - fleet_path.stat().st_mtime) / 86400)
    max_examples = int(THRESHOLDS["max_examples"])
    findings: list[dict] = []

    def warning(code: str, message: str, count: int,
                examples: list | None = None) -> None:
        finding = {"code": code, "severity": "warning",
                   "message": message, "count": count}
        if examples:
            finding["examples"] = examples[:max_examples]
        findings.append(finding)

    if not observed:
        warning("observed_vehicles_empty", "No recent vehicle identities found", 1)
    if missing_fleet:
        warning("observed_vehicle_missing_fleet",
                "Recently observed vehicles have no safe fleet match",
                len(missing_fleet), missing_fleet)
    if missing_livery:
        warning("observed_vehicle_missing_livery",
                "Recently observed fleet vehicles have no complete livery",
                len(missing_livery), missing_livery)
    for name, missing in sorted(missing_blurbs.items()):
        if missing:
            warning(f"observed_vehicle_missing_{name}_blurb",
                    f"Recently observed fleet vehicles lack {name} descriptions",
                    len(missing), missing)
    if fleet_age_days > float(THRESHOLDS["fleet_max_age_days"]):
        warning("fleet_file_old", "Fleet data is older than its report threshold",
                1, [f"{fleet_age_days:.1f} days"])
    if collapses:
        warning("operator_count_collapse",
                "An operator's active fleet count collapsed since the prior report",
                len(collapses), collapses)
    if missing_localities:
        warning("timetable_stop_missing_locality",
                "Timetable stops are absent from the locality lookup",
                len(missing_localities), missing_localities)

    model_context: dict[str, object]
    if model_context_path.is_file():
        contexts = load_json(model_context_path, dict)
        fleet_models = {
            str((record.get("vehicle_type") or {}).get("name") or "").strip()
            for record in matched.values()
            if isinstance(record.get("vehicle_type") or {}, dict)
        } - {""}
        missing_models = sorted(fleet_models - set(map(str, contexts)))
        model_context = {
            "status": "configured",
            "observed_models": len(fleet_models),
            "missing": len(missing_models),
            "missing_examples": missing_models[:max_examples],
        }
        if missing_models:
            warning("observed_model_missing_context",
                    "Observed vehicle models lack curated model context",
                    len(missing_models), missing_models)
    else:
        model_context = {
            "status": "not_configured",
            "note": "Model-context detection begins when WP6 adds the file",
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated.isoformat(),
        "mode": "report_only",
        "status": "warning" if findings else "clean",
        "thresholds": dict(THRESHOLDS),
        "summary": {
            "observed_identities": len(observed),
            "matched_fleet_identities": len(matched),
            "missing_fleet": len(missing_fleet),
            "missing_livery": len(missing_livery),
            "missing_blurbs": {
                name: len(values) for name, values in missing_blurbs.items()
            },
            "missing_stop_localities": len(missing_localities),
            "operator_collapses": len(collapses),
            "fleet_age_days": round(fleet_age_days, 2),
        },
        "fleet": {
            "records": len(records),
            "active": len(active_records),
            "active_by_operator": active_by_operator,
            "observed_by_operator": observed_by_operator,
            "age_days": round(fleet_age_days, 2),
            "operator_collapses": collapses,
        },
        "enrichment": {
            "missing_fleet_examples": missing_fleet[:max_examples],
            "missing_livery_examples": missing_livery[:max_examples],
            "missing_blurbs": {
                name: {"count": len(values), "examples": values[:max_examples]}
                for name, values in sorted(missing_blurbs.items())
            },
        },
        "stops": {
            "timetable": len(stop_codes),
            "localities": len(locality_keys),
            "missing": len(missing_localities),
            "missing_examples": missing_localities[:max_examples],
        },
        "model_context": model_context,
        "findings": findings,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--live-db", type=Path, default=DEFAULT_LIVE_DB)
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    parser.add_argument("--timetable-db", type=Path,
                        default=DEFAULT_TIMETABLE_DB)
    parser.add_argument("--fleet", type=Path, default=DEFAULT_FLEET)
    parser.add_argument("--localities", type=Path, default=DEFAULT_LOCALITIES)
    parser.add_argument("--in-service-descriptions", type=Path, required=True)
    parser.add_argument("--waiting-descriptions", type=Path, required=True)
    parser.add_argument("--depot-descriptions", type=Path, required=True)
    parser.add_argument("--model-context", type=Path,
                        default=DEFAULT_MODEL_CONTEXT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report(
            output=args.output,
            live_db=args.live_db,
            audit_db=args.audit_db,
            timetable_db=args.timetable_db,
            fleet_path=args.fleet,
            localities_path=args.localities,
            description_paths={
                "in_service": args.in_service_descriptions,
                "waiting": args.waiting_descriptions,
                "depot": args.depot_descriptions,
            },
            model_context_path=args.model_context,
        )
        atomic_json(args.output, report)
        summary = report["summary"]
        print(
            f"data health {report['status']} (report-only): "
            f"{summary['observed_identities']} observed, "
            f"{len(report['findings'])} finding(s)")
        return 0
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        print(f"data health failed safely: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

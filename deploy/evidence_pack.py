#!/usr/bin/env python3
"""Create a bounded private evidence pack for suspicious bus readings.

The command reads the collector audit and timetable databases without changing
them.  It writes one private JSON file containing only saved, normalised clues;
the raw SIRI feed was deliberately never retained.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_AUDIT_DB = Path("/var/lib/bristolbusbot/collector/audit.db")
DEFAULT_TIMETABLE_DB = Path("/var/lib/bristolbusbot/pipeline/timetable.db")
MAX_RECEIPTS = 25
MAX_OBSERVATIONS_PER_RECEIPT = 20
MAX_POLLS_PER_RECEIPT = 20
MAX_TIMETABLE_STOPS = 100
MAX_OUTPUT_BYTES = 512 * 1024
PUBLIC_DIRECTORY_NAMES = {
    "audit-site", "bus-audit-repo", "html", "public", "public_html",
    "weca-bus-audit", "www",
}


class EvidencePackError(RuntimeError):
    """An expected, user-facing reason why a pack cannot be created."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def open_read_only(path: Path) -> sqlite3.Connection:
    try:
        uri = path.expanduser().resolve(strict=True).as_uri() + "?mode=ro"
    except FileNotFoundError as exc:
        raise EvidencePackError(f"database does not exist: {path}") from exc
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA query_only=ON")
    return connection


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def require_columns(connection: sqlite3.Connection, table: str,
                    required: set[str]) -> None:
    if not table_exists(connection, table):
        raise EvidencePackError(f"{table} is not present in the audit database")
    columns = {row[1] for row in connection.execute(
        f"PRAGMA table_info({table})")}
    missing = required - columns
    if missing:
        raise EvidencePackError(
            f"{table} is missing required columns: {', '.join(sorted(missing))}")


def normalise_date(value: str | None) -> str | None:
    if value is None:
        return None
    compact = value.strip().replace("-", "")
    try:
        parsed = datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise EvidencePackError("--date must be YYYYMMDD or YYYY-MM-DD") from exc
    return parsed.strftime("%Y%m%d")


def clean_selector(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    result = value.strip()
    if not result:
        raise EvidencePackError(f"--{name} cannot be blank")
    if len(result) > 256:
        raise EvidencePackError(f"--{name} is unexpectedly long")
    return result


def parse_json_list(value: object, *, evidence_id: str,
                    field: str) -> list:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError) as exc:
        raise EvidencePackError(
            f"saved receipt {evidence_id} has invalid {field}") from exc
    if not isinstance(parsed, list):
        raise EvidencePackError(
            f"saved receipt {evidence_id} has non-list {field}")
    return parsed


def representative_rows(rows: list[dict], limit: int) -> list[dict]:
    """Choose deterministic rows spread across the selected time range."""
    if len(rows) <= limit:
        return rows
    if limit == 1:
        return [rows[len(rows) // 2]]
    indices = [round(index * (len(rows) - 1) / (limit - 1))
               for index in range(limit)]
    return [rows[index] for index in indices]


def select_receipts(connection: sqlite3.Connection, selectors: dict) -> list[dict]:
    clauses: list[str] = []
    parameters: list[str] = []
    if selectors.get("date"):
        clauses.append("service_date=?")
        parameters.append(selectors["date"])
    if selectors.get("operator"):
        clauses.append("operator=?")
        parameters.append(selectors["operator"])
    if selectors.get("bus"):
        clauses.append("vehicle_ref=?")
        parameters.append(selectors["bus"])
    if selectors.get("trip"):
        clauses.append("(chosen_trip_id=? OR journey_ref=?)")
        parameters.extend([selectors["trip"], selectors["trip"]])
    if selectors.get("evidence_id"):
        clauses.append("evidence_id=?")
        parameters.append(selectors["evidence_id"])
    if not clauses:
        raise EvidencePackError(
            "choose at least one of --date, --bus, --trip or --evidence-id")
    query = (
        "SELECT * FROM matching_evidence WHERE " + " AND ".join(clauses)
        + " ORDER BY captured_at, evidence_id"
    )
    return [dict(row) for row in connection.execute(query, parameters)]


def receipt_decision(receipt: dict, alternatives: list) -> dict:
    return {
        "chosen": {
            "trip_id": receipt.get("chosen_trip_id"),
            "route_id": receipt.get("timetable_route_id"),
            "service_id": receipt.get("timetable_service_id"),
            "direction_id": receipt.get("timetable_direction_id"),
            "timetable_edition": receipt.get("timetable_edition"),
            "match_tier": receipt.get("match_tier"),
        },
        "candidate_count": receipt.get("candidate_count"),
        "candidates_truncated_when_saved": bool(
            receipt.get("candidates_truncated")),
        "other_journeys_considered": alternatives,
        "other_journeys_stored": len(alternatives),
    }


def classify(receipt: dict, reasons: list[str], calculation_reasons: list[str],
             alternatives: list[dict]) -> dict:
    reasons_set = set(reasons)
    calculation_set = set(calculation_reasons)
    signals: list[str] = []
    alternative_possibilities: list[str] = []

    if "direction_changed_within_run" in reasons_set:
        signals.append("The reported direction changed during one vehicle run.")
    if "match_changed_within_run" in reasons_set:
        signals.append("The chosen timetable trip changed during one vehicle run.")
    if "outside_measurement_gate" in calculation_set:
        signals.append("The bus position was too far from every usable timetable stop.")
    if "sanity_rejected" in reasons_set:
        signals.append(
            "The calculated time difference was outside the accepted -15 to +90 minute range.")
    if "extreme_delay" in reasons_set:
        signals.append("The accepted reading showed an unusually large delay.")

    editions = {
        str(value) for value in [receipt.get("timetable_edition"), *[
            item.get("timetable_edition") for item in alternatives
            if isinstance(item, dict)]]
        if value
    }

    if "match_changed_within_run" in reasons_set:
        category, confidence = "wrong_journey", "medium"
        explanation = (
            "A different timetable journey was selected during the same vehicle run. "
            "That is a strong clue, but it does not prove which selection was wrong.")
        alternative_possibilities.extend([
            "timetable_overlap", "operator_feed_problem"])
    elif "direction_changed_within_run" in reasons_set:
        category, confidence = "wrong_direction", "medium"
        explanation = (
            "The feed direction changed during the same vehicle run. This may be a "
            "wrong-direction reading or an operator feed correction.")
        alternative_possibilities.append("operator_feed_problem")
    elif "outside_measurement_gate" in calculation_set:
        category, confidence = "gps_problem", "medium"
        explanation = (
            "The reported position did not sit close enough to the matched journey's "
            "stops. A GPS or journey-identity problem is plausible.")
        alternative_possibilities.append("wrong_journey")
    elif len(editions) > 1:
        category, confidence = "timetable_overlap", "medium"
        explanation = (
            "The saved candidate journeys came from more than one timetable edition, "
            "so overlapping registrations may have affected the choice.")
        alternative_possibilities.append("wrong_journey")
    elif "sanity_rejected" in reasons_set:
        category, confidence = "clock_problem", "low"
        explanation = (
            "The feed time and the selected schedule disagreed by an implausible amount. "
            "A clock, service-date, timetable or journey-reference problem needs review.")
        alternative_possibilities.extend([
            "wrong_journey", "timetable_overlap", "operator_feed_problem"])
    elif "extreme_delay" in reasons_set:
        exact = receipt.get("match_tier") == "exact"
        single = int(receipt.get("candidate_count") or 0) == 1
        category = "real_delay" if exact and single else "inconclusive"
        confidence = "medium" if category == "real_delay" else "low"
        explanation = (
            "The reading showed a large delay and the journey reference matched exactly "
            "with no competing candidate. It is credible evidence of a real delay, but "
            "still not independent proof."
            if category == "real_delay" else
            "The reading showed a large delay, but the saved match was not strong enough "
            "to distinguish a real delay from a journey or feed problem.")
        alternative_possibilities.extend([
            "wrong_journey", "clock_problem", "operator_feed_problem"])
    else:
        category, confidence = "inconclusive", "low"
        explanation = "The saved clues do not support one cause more strongly than the others."

    return {
        "likely_cause": category,
        "confidence": confidence,
        "plain_english": explanation,
        "supporting_signals": signals,
        "alternative_possibilities": sorted(set(alternative_possibilities)),
        "not_assessable_from_saved_receipt": ["old_repeated_data"],
    }


def expected_trip(audit: sqlite3.Connection, receipt: dict) -> dict:
    if not table_exists(audit, "expected_trips"):
        return {"available": False, "reason": "expected_trips table is unavailable"}
    row = audit.execute(
        "SELECT * FROM expected_trips WHERE service_date=? AND trip_id=?",
        (receipt.get("service_date"), receipt.get("chosen_trip_id")),
    ).fetchone()
    if row is None:
        return {
            "available": False,
            "reason": "no retained scheduled-trip row matches this date and chosen trip",
        }
    return {"available": True, "row": dict(row)}


def nearby_observations(audit: sqlite3.Connection, receipt: dict) -> dict:
    if not table_exists(audit, "timepoint_observations"):
        return {"available": False, "reason": "observation table is unavailable", "rows": []}
    rows = [dict(row) for row in audit.execute(
        """SELECT service_date, operator, route, trip_id, siri_journey_ref,
                  stop_sequence, stop_code, scheduled_local, observed_delay_s,
                  on_time, gps_distance_m, recorded_at, vehicle_ref, is_origin,
                  match_tier
             FROM timepoint_observations
            WHERE service_date=?
              AND (trip_id=? OR
                   (vehicle_ref=? AND datetime(recorded_at) BETWEEN
                       datetime(?, '-30 minutes') AND datetime(?, '+30 minutes')))
            ORDER BY ABS(julianday(recorded_at) - julianday(?)), stop_sequence
            LIMIT ?""",
        (receipt.get("service_date"), receipt.get("chosen_trip_id"),
         receipt.get("vehicle_ref"), receipt.get("captured_at"),
         receipt.get("captured_at"), receipt.get("captured_at"),
         MAX_OBSERVATIONS_PER_RECEIPT),
    )]
    return {
        "available": bool(rows),
        "reason": None if rows else "no related retained audit observations were found",
        "rows": rows,
        "row_limit": MAX_OBSERVATIONS_PER_RECEIPT,
        "note": "These are the closest readings currently retained per trip and timing point; a later closer reading may have replaced the original one.",
    }


def nearby_polls(audit: sqlite3.Connection, receipt: dict) -> dict:
    if not table_exists(audit, "poll_log"):
        return {"available": False, "reason": "poll log is unavailable", "rows": []}
    rows = [dict(row) for row in audit.execute(
        """SELECT poll_at, ok, vehicles_total, candidates, matched, obs_written,
                  dropped_insane, stale, evidence_written, evidence_dropped
             FROM poll_log
            WHERE datetime(poll_at) BETWEEN datetime(?, '-2 minutes')
                                        AND datetime(?, '+2 minutes')
            ORDER BY poll_at LIMIT ?""",
        (receipt.get("captured_at"), receipt.get("captured_at"),
         MAX_POLLS_PER_RECEIPT),
    )]
    return {
        "available": bool(rows),
        "reason": None if rows else "no nearby retained collector poll rows were found",
        "rows": rows,
        "row_limit": MAX_POLLS_PER_RECEIPT,
    }


def current_timetable_trip(timetable: sqlite3.Connection,
                           receipt: dict) -> dict:
    trip_id = receipt.get("chosen_trip_id")
    if not trip_id:
        return {"available": False, "reason": "receipt has no chosen trip ID"}
    row = timetable.execute(
        """SELECT t.trip_id, t.route_id, t.service_id, t.trip_headsign,
                  t.trip_short_name, t.direction_id, t.block_id,
                  t.vehicle_journey_code, r.route_short_name, a.agency_noc
             FROM trips t
             LEFT JOIN routes r ON r.route_id=t.route_id
             LEFT JOIN agency a ON a.agency_id=r.agency_id
            WHERE t.trip_id=?""", (trip_id,)).fetchone()
    if row is None:
        return {
            "available": False,
            "reason": "the exact saved trip ID is not in the current timetable database",
            "note": "The saved receipt remains the capture-time evidence; a current timetable is never substituted for a missing historical one.",
        }
    stops = [dict(item) for item in timetable.execute(
        """SELECT st.stop_sequence, st.arrival_time, st.departure_time,
                  st.timepoint, st.pickup_type, st.drop_off_type,
                  s.stop_id, s.stop_code, s.stop_name
             FROM stop_times st
             LEFT JOIN stops s ON s.stop_id=st.stop_id
            WHERE st.trip_id=? ORDER BY st.stop_sequence LIMIT ?""",
        (trip_id, MAX_TIMETABLE_STOPS),
    )]
    total_stops = timetable.execute(
        "SELECT COUNT(*) FROM stop_times WHERE trip_id=?", (trip_id,)
    ).fetchone()[0]
    edition = None
    if table_exists(timetable, "route_service_editions"):
        edition_row = timetable.execute(
            """SELECT edition_start, effective_end
                 FROM route_service_editions
                WHERE route_id=? AND edition_start<=? AND effective_end>=?
                ORDER BY edition_start DESC LIMIT 1""",
            (row["route_id"], receipt.get("service_date"),
             receipt.get("service_date")),
        ).fetchone()
        if edition_row:
            edition = dict(edition_row)
    return {
        "available": True,
        "source": "current timetable database",
        "trip": dict(row),
        "service_date_edition": edition,
        "receipt_edition_matches_current": (
            None if edition is None or not receipt.get("timetable_edition")
            else edition.get("edition_start") == receipt.get("timetable_edition")
        ),
        "stops": stops,
        "stop_count": total_stops,
        "stops_truncated": total_stops > len(stops),
        "stop_limit": MAX_TIMETABLE_STOPS,
    }


def public_output_reason(path: Path) -> str | None:
    parts = {part.lower() for part in path.parts}
    matched = sorted(parts & PUBLIC_DIRECTORY_NAMES)
    if matched:
        return f"path contains known public directory name '{matched[0]}'"
    value = path.as_posix().lower().rstrip("/")
    for root in ("/var/www", "/srv/http", "/srv/www"):
        if value == root or value.startswith(root + "/"):
            return f"path is under public web root {root}"
    return None


def safe_output_path(path: Path, *, audit_db: Path, timetable_db: Path,
                     force: bool) -> Path:
    expanded = path.expanduser()
    try:
        parent = expanded.parent.resolve(strict=True)
    except FileNotFoundError as exc:
        raise EvidencePackError(
            f"output directory does not exist: {expanded.parent}") from exc
    output = parent / expanded.name
    if not expanded.name or expanded.name in {".", ".."}:
        raise EvidencePackError("--output must name a JSON file")
    if output.suffix.lower() != ".json":
        raise EvidencePackError("--output must end in .json")
    reason = public_output_reason(output)
    if reason:
        raise EvidencePackError(
            f"refusing public output path ({reason}); choose a private directory")
    try:
        inputs = {
            audit_db.expanduser().resolve(strict=True),
            timetable_db.expanduser().resolve(strict=True),
        }
    except FileNotFoundError as exc:
        raise EvidencePackError(f"database does not exist: {exc.filename}") from exc
    if output in inputs:
        raise EvidencePackError("output cannot replace an input database")
    if output.is_symlink():
        raise EvidencePackError("refusing to replace a symbolic-link output")
    if output.exists() and not force:
        raise EvidencePackError("output already exists; choose another name or use --force")
    return output


def serialised(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2)
            + "\n").encode("utf-8")


def atomic_private_json(path: Path, payload: dict, *, force: bool = False) -> int:
    content = serialised(payload)
    if len(content) > MAX_OUTPUT_BYTES:
        raise EvidencePackError(
            f"evidence pack would exceed the {MAX_OUTPUT_BYTES}-byte safety limit")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            os.replace(temporary_path, path)
        else:
            try:
                os.link(temporary_path, path)
            except FileExistsError as exc:
                raise EvidencePackError(
                    "output appeared while the pack was being built; no file was replaced") from exc
            temporary_path.unlink()
        os.chmod(path, 0o600)
    finally:
        temporary_path.unlink(missing_ok=True)
    return len(content)


def incident(audit: sqlite3.Connection, timetable: sqlite3.Connection,
             raw_receipt: dict) -> dict:
    evidence_id = str(raw_receipt["evidence_id"])
    reasons = parse_json_list(raw_receipt.get("reasons_json"),
                              evidence_id=evidence_id, field="reasons_json")
    calculation_reasons = parse_json_list(
        raw_receipt.get("calculation_reasons_json"), evidence_id=evidence_id,
        field="calculation_reasons_json")
    alternatives = parse_json_list(
        raw_receipt.get("alternatives_json"), evidence_id=evidence_id,
        field="alternatives_json")
    receipt = {
        key: value for key, value in raw_receipt.items()
        if key not in {"reasons_json", "calculation_reasons_json",
                       "alternatives_json"}
    }
    receipt["reasons"] = reasons
    receipt["calculation_reasons"] = calculation_reasons
    receipt["candidates_truncated"] = bool(receipt.get("candidates_truncated"))
    return {
        "evidence_id": evidence_id,
        "assessment": classify(receipt, reasons, calculation_reasons, alternatives),
        "saved_receipt": receipt,
        "matching_decision": receipt_decision(receipt, alternatives),
        "related_audit_observations": nearby_observations(audit, receipt),
        "scheduled_trip_snapshot": expected_trip(audit, receipt),
        "current_timetable_journey": current_timetable_trip(timetable, receipt),
        "nearby_collector_polls": nearby_polls(audit, receipt),
    }


def categories_reference() -> dict:
    return {
        "real_delay": "credible evidence of a real delay, not independent proof",
        "wrong_journey": "the system may have selected the wrong timetable journey",
        "wrong_direction": "the reported or selected direction may be wrong",
        "old_repeated_data": "not assessable because discarded stale raw snapshots were never retained",
        "timetable_overlap": "overlapping or changed timetable editions may have affected matching",
        "clock_problem": "feed time, service date or schedule time may disagree",
        "gps_problem": "reported position may disagree with the selected journey",
        "operator_feed_problem": "possible feed identity/timestamp problem; never proven by a receipt alone",
        "inconclusive": "saved clues do not support one cause strongly enough",
    }


def build_bundle(audit_db: Path, timetable_db: Path, selectors: dict, *,
                 now: datetime | None = None,
                 receipt_limit: int = MAX_RECEIPTS) -> dict:
    if not 1 <= receipt_limit <= MAX_RECEIPTS:
        raise EvidencePackError(
            f"receipt limit must be between 1 and {MAX_RECEIPTS}")
    with closing(open_read_only(audit_db)) as audit, closing(
            open_read_only(timetable_db)) as timetable:
        require_columns(audit, "matching_evidence", {
            "evidence_id", "captured_at", "service_date", "reasons_json",
            "calculation_reasons_json", "operator", "route", "vehicle_ref",
            "journey_ref", "chosen_trip_id", "match_tier", "candidate_count",
            "candidates_truncated", "timetable_edition", "alternatives_json",
        })
        for table in ("trips", "routes", "agency", "stop_times", "stops"):
            if not table_exists(timetable, table):
                raise EvidencePackError(
                    f"{table} is not present in the timetable database")
        all_receipts = select_receipts(audit, selectors)
        if not all_receipts:
            raise EvidencePackError("no saved matching receipts matched those selectors")
        chosen = representative_rows(all_receipts, receipt_limit)
        incidents = [incident(audit, timetable, receipt) for receipt in chosen]

    category_counts = Counter(
        item["assessment"]["likely_cause"] for item in incidents)
    counts = dict(sorted(category_counts.items(),
                         key=lambda pair: (-pair[1], pair[0])))
    found = len(all_receipts)
    included = len(incidents)
    truncated = found > included
    plain = f"Found {found} saved odd-reading receipt{'s' if found != 1 else ''}. "
    plain += f"Included {included}"
    if truncated:
        plain += " spread across the selected time range"
    plain += f". Current labels: {', '.join(f'{key} {value}' for key, value in counts.items())}."
    return {
        "summary": {
            "plain_english": plain,
            "matching_receipts": found,
            "included_receipts": included,
            "receipts_truncated": truncated,
            "receipt_selection": (
                "evenly_spaced_across_time" if truncated else "all_matches"),
            "likely_cause_counts": counts,
        },
        "schema_version": 1,
        "generated_at": (now or utcnow()).astimezone(timezone.utc).isoformat(),
        "mode": "private_read_only_diagnostic",
        "selectors": {key: value for key, value in selectors.items() if value},
        "limits": {
            "receipt_limit": receipt_limit,
            "observation_rows_per_receipt": MAX_OBSERVATIONS_PER_RECEIPT,
            "poll_rows_per_receipt": MAX_POLLS_PER_RECEIPT,
            "timetable_stops_per_receipt": MAX_TIMETABLE_STOPS,
            "maximum_output_bytes": MAX_OUTPUT_BYTES,
        },
        "category_reference": categories_reference(),
        "incidents": incidents,
        "limitations": [
            "This pack contains private diagnostic evidence, not official operator data.",
            "A likely-cause label is a triage judgement, not proof.",
            "The raw SIRI feed and discarded stale snapshots were deliberately never retained.",
            "Saved matching receipts are capped at 250 per service day and 5,000 in total.",
            "Old audit observations are pruned and closest readings can be replaced by later closer readings.",
            "Current timetable detail is included only when the exact saved trip ID still exists and is clearly labelled current.",
            "Nothing in this command changes either database or any public statistic.",
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="service date: YYYYMMDD or YYYY-MM-DD")
    parser.add_argument("--bus", help="exact vehicle reference from the feed")
    parser.add_argument("--trip", help="exact chosen timetable trip or SIRI journey reference")
    parser.add_argument("--evidence-id", help="exact saved evidence receipt ID")
    parser.add_argument("--operator", help="optional exact operator code")
    parser.add_argument("--output", required=True, type=Path,
                        help="new private .json file to create")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing private output file")
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB,
                        help=argparse.SUPPRESS)
    parser.add_argument("--timetable-db", type=Path,
                        default=DEFAULT_TIMETABLE_DB, help=argparse.SUPPRESS)
    parser.add_argument("--receipt-limit", type=int, default=MAX_RECEIPTS,
                        help=f"maximum saved receipts to include (1-{MAX_RECEIPTS})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        selectors = {
            "date": normalise_date(args.date),
            "bus": clean_selector(args.bus, "bus"),
            "trip": clean_selector(args.trip, "trip"),
            "evidence_id": clean_selector(args.evidence_id, "evidence-id"),
            "operator": clean_selector(args.operator, "operator"),
        }
        output = safe_output_path(
            args.output, audit_db=args.audit_db,
            timetable_db=args.timetable_db, force=args.force)
        payload = build_bundle(
            args.audit_db, args.timetable_db, selectors,
            receipt_limit=args.receipt_limit)
        size = atomic_private_json(output, payload, force=args.force)
    except (EvidencePackError, OSError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        f"Created private evidence pack: {output} "
        f"({payload['summary']['included_receipts']}/"
        f"{payload['summary']['matching_receipts']} receipts, {size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

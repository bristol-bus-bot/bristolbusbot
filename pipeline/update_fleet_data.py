#!/usr/bin/env python3
"""Build and validate a fleet-data candidate without touching live data.

The production systemd unit supplies fixed paths for the durable live fleet,
the isolated shadow candidate and the review report.  This program has no
promotion mode: accepting a candidate still requires the separate guarded
promotion helper and explicit operator approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


REPO_DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
if REPO_DEPLOY.is_dir():
    sys.path.insert(0, str(REPO_DEPLOY))

from enrichment_contracts import (  # noqa: E402
    EnrichmentContractError,
    compare_fleet,
    validate_fleet,
)


SOURCE_HOST = "bustimes.org"
SOURCE_PATH = "/api/vehicles/"
SOURCE_USER_AGENT = (
    "BristolBusBot-fleet-shadow/1.0 (+https://bristolbuses.live/)"
)
MAXIMUM_PAGE_BYTES = 8 * 1024 * 1024
MAXIMUM_PAGES_PER_OPERATOR = 100
WHITE_LIVERIES = {"#fff", "#ffffff", "white"}


@dataclass(frozen=True)
class Operator:
    code: str
    empty_reason: str | None = None


KNOWN_EMPTY = (
    "no records in the commissioned live baseline; kept as an explicit watch"
)
VITR_TRANSITION = (
    "bustimes moved these records to KEMT; every live VITR id must be present "
    "under KEMT before the transition is accepted"
)
OPERATOR_TRANSITIONS = {"VITR": "KEMT"}
OPERATORS: tuple[Operator, ...] = (
    Operator("FBRI"),
    Operator("SSWL"),
    Operator("SDVN"),
    Operator("SCGL"),
    Operator("NATX"),
    Operator("KEMT"),
    Operator("VITR", VITR_TRANSITION),
    Operator("FSRV"),
    Operator("NWPT"),
    Operator("ABUS"),
    Operator("BDOL"),
    Operator("CTCO"),
    Operator("FRMN"),
    Operator("TDTR"),
    Operator("EZMT", KNOWN_EMPTY),
    Operator("PULH"),
    Operator("FLIX", KNOWN_EMPTY),
    Operator("EUTX", KNOWN_EMPTY),
    Operator("TYSW", KNOWN_EMPTY),
    Operator("COAC", KNOWN_EMPTY),
    Operator("LTRV", KNOWN_EMPTY),
)


class FleetShadowError(RuntimeError):
    """A candidate could not be built safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise FleetShadowError("unsafe_path", f"{label} is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise FleetShadowError("unsafe_path", f"{label} is not a safe directory")
    return info


def _regular_bytes(path: Path, label: str, maximum: int) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise FleetShadowError("unsafe_path", f"{label} is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise FleetShadowError("unsafe_path", f"{label} is not a regular file")
    if info.st_size <= 0 or info.st_size > maximum:
        raise FleetShadowError("unsafe_path", f"{label} has an unsafe size")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise FleetShadowError("unsafe_path", f"{label} is unreadable") from exc


def _prepare_output(path: Path, label: str) -> None:
    _directory(path.parent, f"{label} directory")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise FleetShadowError("unsafe_path", f"{label} is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise FleetShadowError("unsafe_path", f"{label} is not a regular file")
    path.unlink()


def _discard_candidate(candidate: Path, live: Path) -> None:
    """Remove only a separate regular candidate; never follow or remove live."""
    if candidate.resolve(strict=False) == live.resolve(strict=False):
        return
    try:
        info = candidate.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    if stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        candidate.unlink(missing_ok=True)


def _atomic_bytes(path: Path, raw: bytes, mode: int = 0o600) -> None:
    _directory(path.parent, "output directory")
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    _atomic_bytes(path, raw, 0o640)


def _source_url(operator: str) -> str:
    query = urllib.parse.urlencode({
        "operator": operator,
        "format": "json",
        "limit": "100",
    })
    return f"https://{SOURCE_HOST}{SOURCE_PATH}?{query}"


def _safe_source_url(value: str, operator: str) -> str:
    candidate = urllib.parse.urljoin(_source_url(operator), value)
    parsed = urllib.parse.urlsplit(candidate)
    if parsed.scheme != "https" or parsed.hostname != SOURCE_HOST \
            or parsed.port not in (None, 443) or parsed.username is not None \
            or parsed.password is not None or parsed.path != SOURCE_PATH:
        raise FleetShadowError(
            "unsafe_source_url", f"operator {operator} returned an unsafe next URL")
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    if query.get("operator") != [operator]:
        raise FleetShadowError(
            "unsafe_source_url", f"operator {operator} pagination changed operator")
    return candidate


def _page(
    opener: object,
    url: str,
    operator: str,
    *,
    timeout: float,
    attempts: int,
    sleep: Callable[[float], None],
) -> dict[str, object]:
    last_error = "unknown source failure"
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": SOURCE_USER_AGENT},
        )
        try:
            with opener.open(request, timeout=timeout) as response:  # type: ignore[attr-defined]
                final_url = response.geturl()
                _safe_source_url(final_url, operator)
                raw = response.read(MAXIMUM_PAGE_BYTES + 1)
                if len(raw) > MAXIMUM_PAGE_BYTES:
                    raise FleetShadowError(
                        "source_response_too_large",
                        f"operator {operator} returned an oversized page",
                    )
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise FleetShadowError(
                    "malformed_source", f"operator {operator} returned non-object JSON")
            return value
        except FleetShadowError:
            raise
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = type(exc).__name__
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise FleetShadowError(
                "malformed_source", f"operator {operator} returned invalid JSON")
        if attempt < attempts:
            sleep(min(2 ** (attempt - 1), 4))
    raise FleetShadowError(
        "source_failed",
        f"operator {operator} failed after {attempts} attempts ({last_error})",
    )


def fetch_operator(
    operator: Operator,
    *,
    opener: object,
    timeout: float,
    attempts: int,
    pace: float,
    sleep: Callable[[float], None],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    records: list[dict[str, object]] = []
    pages = 0
    next_url: str | None = _source_url(operator.code)
    visited: set[str] = set()
    while next_url is not None:
        next_url = _safe_source_url(next_url, operator.code)
        if next_url in visited:
            raise FleetShadowError(
                "pagination_loop", f"operator {operator.code} repeated a page")
        visited.add(next_url)
        pages += 1
        if pages > MAXIMUM_PAGES_PER_OPERATOR:
            raise FleetShadowError(
                "pagination_limit", f"operator {operator.code} exceeded page limit")
        value = _page(
            opener, next_url, operator.code, timeout=timeout,
            attempts=attempts, sleep=sleep)
        results = value.get("results")
        if not isinstance(results, list):
            raise FleetShadowError(
                "malformed_source",
                f"operator {operator.code} response has no results list",
            )
        for index, record in enumerate(results):
            if not isinstance(record, dict):
                raise FleetShadowError(
                    "malformed_source",
                    f"operator {operator.code} record {index} is not an object",
                )
            source_operator = record.get("operator")
            if not isinstance(source_operator, dict) \
                    or str(source_operator.get("id") or "").upper() != operator.code:
                raise FleetShadowError(
                    "operator_mismatch",
                    f"operator {operator.code} returned a record for another operator",
                )
            withdrawn = record.get("withdrawn")
            if not isinstance(withdrawn, bool):
                raise FleetShadowError(
                    "malformed_source",
                    f"operator {operator.code} returned an invalid withdrawn flag",
                )
            if not withdrawn:
                records.append(record)
        following = value.get("next")
        if following is not None and not isinstance(following, str):
            raise FleetShadowError(
                "malformed_source", f"operator {operator.code} next link is invalid")
        next_url = following or None
        if next_url is not None:
            sleep(pace)

    if not records and operator.empty_reason is None:
        raise FleetShadowError(
            "unexplained_empty",
            f"operator {operator.code} returned no active vehicles without an explanation",
        )
    return records, {
        "code": operator.code,
        "status": "fetched",
        "pages": pages,
        "active_records": len(records),
        "empty_reason": operator.empty_reason if not records else None,
    }


def _has_livery(record: Mapping[str, object]) -> bool:
    value = record.get("livery")
    if not isinstance(value, dict):
        return False
    left = str(value.get("left") or "").strip().lower()
    right = str(value.get("right") or "").strip().lower()
    return bool(left and right and left not in WHITE_LIVERIES
                and right not in WHITE_LIVERIES)


def _livery_summary(records: Iterable[Mapping[str, object]]) -> dict[str, object]:
    total = 0
    complete = 0
    by_operator: dict[str, dict[str, int]] = {}
    for record in records:
        if record.get("withdrawn") is True:
            continue
        total += 1
        operator = record.get("operator")
        code = str(operator.get("id") or "") if isinstance(operator, dict) else ""
        counts = by_operator.setdefault(code, {"active": 0, "complete": 0})
        counts["active"] += 1
        if _has_livery(record):
            complete += 1
            counts["complete"] += 1
    return {
        "active": total,
        "complete": complete,
        "missing_or_incomplete": total - complete,
        "by_operator": dict(sorted(by_operator.items())),
    }


def _difference(
    live: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    live_by_id = {int(record["id"]): record for record in live}
    candidate_by_id = {int(record["id"]): record for record in candidate}
    added = sorted(set(candidate_by_id) - set(live_by_id))
    removed = sorted(set(live_by_id) - set(candidate_by_id))
    changed = sorted(
        key for key in set(live_by_id) & set(candidate_by_id)
        if live_by_id[key] != candidate_by_id[key]
    )

    def identity(record: Mapping[str, object]) -> dict[str, object]:
        operator = record.get("operator")
        return {
            "id": int(record["id"]),
            "operator": str(operator.get("id") or "")
            if isinstance(operator, dict) else "",
            "fleet_code": str(record.get("fleet_code") or ""),
            "registration": str(record.get("reg") or ""),
        }

    return {
        "added": len(added),
        "added_records": [identity(candidate_by_id[key]) for key in added],
        "removed": len(removed),
        "removed_records": [identity(live_by_id[key]) for key in removed],
        "changed": len(changed),
        "changed_records": [
            {
                "id": key,
                "live_operator": identity(live_by_id[key])["operator"],
                "candidate_operator": identity(candidate_by_id[key])["operator"],
                "fields": sorted(
                    field for field in
                    set(live_by_id[key]) | set(candidate_by_id[key])
                    if live_by_id[key].get(field) != candidate_by_id[key].get(field)
                ),
            }
            for key in changed
        ],
        "live_livery": _livery_summary(live),
        "candidate_livery": _livery_summary(candidate),
    }


def _comparison_baseline(
    live: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
    live_summary: Mapping[str, object],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Apply only explicit, exact-ID operator transitions to comparison counts."""
    adjusted = json.loads(json.dumps(live_summary))
    transitions: list[dict[str, object]] = []
    for legacy, replacement in OPERATOR_TRANSITIONS.items():
        live_legacy_ids = {
            int(record["id"])
            for record in live
            if record.get("withdrawn") is not True
            and isinstance(record.get("operator"), dict)
            and str(record["operator"].get("id") or "").upper() == legacy  # type: ignore[union-attr]
        }
        if not live_legacy_ids:
            continue
        candidate_legacy_ids = {
            int(record["id"])
            for record in candidate
            if record.get("withdrawn") is not True
            and isinstance(record.get("operator"), dict)
            and str(record["operator"].get("id") or "").upper() == legacy  # type: ignore[union-attr]
        }
        if candidate_legacy_ids:
            transitions.append({
                "legacy": legacy,
                "replacement": replacement,
                "status": "source-still-uses-legacy-id",
                "live_legacy_records": len(live_legacy_ids),
                "candidate_legacy_records": len(candidate_legacy_ids),
            })
            continue
        replacement_ids = {
            int(record["id"])
            for record in candidate
            if record.get("withdrawn") is not True
            and isinstance(record.get("operator"), dict)
            and str(record["operator"].get("id") or "").upper() == replacement  # type: ignore[union-attr]
        }
        missing = sorted(live_legacy_ids - replacement_ids)
        if missing:
            raise FleetShadowError(
                "operator_transition_incomplete",
                f"operator {legacy} moved to {replacement}, but "
                f"{len(missing)} live vehicle ids are missing from the replacement",
            )
        for field in ("operator_counts", "active_operator_counts"):
            counts = adjusted.get(field)
            if not isinstance(counts, dict):
                raise FleetShadowError(
                    "live_contract", f"live {field} summary is invalid")
            moved = int(counts.pop(legacy, 0))
            counts[replacement] = int(counts.get(replacement, 0)) + moved
            adjusted[field] = dict(sorted(counts.items()))
        transitions.append({
            "legacy": legacy,
            "replacement": replacement,
            "status": "exact-id-transition-accepted",
            "live_legacy_records": len(live_legacy_ids),
            "matched_replacement_records": len(live_legacy_ids),
            "missing_ids": 0,
        })
    return adjusted, transitions


def _sort_key(record: Mapping[str, object]) -> tuple[str, str, int]:
    operator = record.get("operator")
    code = str(operator.get("id") or "") if isinstance(operator, dict) else ""
    fleet = str(record.get("fleet_code") or record.get("fleet_number") or "")
    return code, fleet, int(record.get("id") or 0)


def build_shadow(
    *,
    live: Path,
    candidate: Path,
    report_path: Path,
    operators: Sequence[Operator] = OPERATORS,
    opener: object | None = None,
    timeout: float = 20,
    attempts: int = 3,
    pace: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    started = utcnow()
    report: dict[str, object] = {
        "schema": 1,
        "started_at": started,
        "source": f"https://{SOURCE_HOST}{SOURCE_PATH}",
        "mode": "shadow-only",
        "promotion_attempted": False,
        "operators": [],
    }
    try:
        if live.resolve(strict=False) == candidate.resolve(strict=False):
            raise FleetShadowError(
                "unsafe_path", "candidate path must differ from the live fleet path")
        if report_path.resolve(strict=False) in {
                live.resolve(strict=False), candidate.resolve(strict=False)}:
            raise FleetShadowError(
                "unsafe_path", "report path must differ from fleet paths")
        _prepare_output(candidate, "shadow candidate")
        _prepare_output(report_path, "shadow report")
        live_raw = _regular_bytes(live, "live fleet", 64 * 1024 * 1024)
        live_value = json.loads(live_raw)
        if not isinstance(live_value, list):
            raise FleetShadowError("live_contract", "live fleet is not a list")
        live_summary = validate_fleet(live_raw)
        report["live"] = {
            "sha256": digest(live_raw),
            "summary": live_summary,
        }

        source = opener or urllib.request.build_opener()
        fetched: list[dict[str, object]] = []
        operator_results: list[dict[str, object]] = []
        failed = False
        for index, operator in enumerate(operators):
            try:
                records, result = fetch_operator(
                    operator, opener=source, timeout=timeout,
                    attempts=attempts, pace=pace, sleep=sleep)
                fetched.extend(records)
                operator_results.append(result)
            except FleetShadowError as exc:
                failed = True
                operator_results.append({
                    "code": operator.code,
                    "status": "source-failed",
                    "failure_code": exc.code,
                    "message": str(exc),
                })
            if index + 1 < len(operators):
                sleep(pace)
        report["operators"] = operator_results
        if failed:
            raise FleetShadowError(
                "operator_source_failure",
                "one or more configured operators failed; candidate discarded",
            )

        fetched.sort(key=_sort_key)
        candidate_raw = (json.dumps(fetched, indent=2) + "\n").encode()
        candidate_summary = validate_fleet(candidate_raw)
        comparison_live, transitions = _comparison_baseline(
            live_value, fetched, live_summary)
        comparison = compare_fleet(candidate_summary, comparison_live)
        report["candidate"] = {
            "sha256": digest(candidate_raw),
            "summary": candidate_summary,
        }
        report["comparison"] = comparison
        report["operator_transitions"] = transitions
        report["difference"] = _difference(live_value, fetched)
        _atomic_bytes(candidate, candidate_raw)
        report.update({
            "outcome": "accepted-shadow",
            "candidate_written": True,
            "finished_at": utcnow(),
        })
        _atomic_json(report_path, report)
        return report
    except (FleetShadowError, EnrichmentContractError, json.JSONDecodeError,
            OSError) as exc:
        _discard_candidate(candidate, live)
        if isinstance(exc, FleetShadowError):
            code = exc.code
        elif isinstance(exc, OSError):
            code = "io_failure"
        else:
            code = "candidate_contract"
        report.update({
            "outcome": "rejected",
            "candidate_written": False,
            "failure_code": code,
            "message": str(exc),
            "finished_at": utcnow(),
        })
        try:
            _atomic_json(report_path, report)
        except (OSError, FleetShadowError):
            pass
        if isinstance(exc, FleetShadowError):
            raise
        raise FleetShadowError(code, str(exc)) from exc


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--live", type=Path, required=True)
    value.add_argument("--candidate", type=Path, required=True)
    value.add_argument("--report", type=Path, required=True)
    value.add_argument("--timeout-seconds", type=float, default=20)
    value.add_argument("--attempts", type=int, default=3)
    value.add_argument("--pace-seconds", type=float, default=0.5)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not 1 <= args.timeout_seconds <= 60:
        raise SystemExit("timeout must be between 1 and 60 seconds")
    if not 1 <= args.attempts <= 5:
        raise SystemExit("attempts must be between 1 and 5")
    if not 0.1 <= args.pace_seconds <= 5:
        raise SystemExit("pace must be between 0.1 and 5 seconds")
    try:
        report = build_shadow(
            live=args.live,
            candidate=args.candidate,
            report_path=args.report,
            timeout=args.timeout_seconds,
            attempts=args.attempts,
            pace=args.pace_seconds,
        )
    except FleetShadowError as exc:
        print(f"fleet shadow rejected [{exc.code}]: {exc}", file=sys.stderr)
        return 1
    difference = report["difference"]
    print(
        "fleet shadow accepted: "
        f"{report['candidate']['summary']['records']} records; "  # type: ignore[index]
        f"added {difference['added']}, removed {difference['removed']}, "  # type: ignore[index]
        f"changed {difference['changed']}; live data untouched"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

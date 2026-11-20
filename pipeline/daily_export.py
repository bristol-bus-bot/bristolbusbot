#!/usr/bin/env python3
"""Nightly small data export for analysis on Tom's PC (planning #152).

The Pi's own scheduled job writes three small files each morning and pushes
them to a private GitHub repository, the same way the audit publish pushes
the public site. Nobody connects to the Pi to collect them.

* readings: one service day of timing-point readings, expected trips, the
  day's coverage and summary rows, matching receipts and poll health, as a
  gzipped SQLite file. Every query reads one day through an index.
* rare-workings: the bot's rare-working shadow observations first seen that
  day (for #67), as gzipped JSON lines.
* writer-drafts: the writer's training drafts added since the last export
  (for #93), read from a saved byte offset, as gzipped JSON lines.

Each part succeeds or fails on its own; a status file feeds one plain line
into the daily Slack summary. Days missed since 14 Nov (and any missed
later) are caught up a few per night with no manual step. Branches older
than RETENTION_DAYS are deleted from the store by the same job.

    python daily_export.py            # the nightly run
    python daily_export.py --no-push  # build the files only (PC testing)
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

try:
    from scan_secrets import PATTERNS as SECRET_PATTERNS
except ModuleNotFoundError:  # running from the repository
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from scan_secrets import PATTERNS as SECRET_PATTERNS

LONDON = ZoneInfo("Europe/London")
SCHEMA_VERSION = 1
CATCH_UP_FROM = date(2026, 11, 14)     # the PC copies stop here; lapses once filled
RETENTION_DAYS = 120
CATCH_UP_PER_NIGHT = {"readings": 4, "rare-workings": 31}
MAX_PART_BYTES = 40 * 1024 * 1024      # well under GitHub's 100 MB file limit
PARTS = ("readings", "rare-workings", "writer-drafts")

DEFAULT_AUDIT_DB = Path("/var/lib/bristolbusbot/collector/audit.db")
DEFAULT_APP_DB = Path("/var/lib/bristolbusbot/bot/app_data.db")
DEFAULT_TRAINING = Path("/var/lib/bristolbusbot/bot/editorial-usage.json.training.jsonl")
DEFAULT_STATE_DIR = Path("/var/lib/bristolbusbot/daily-export")
DEFAULT_STATUS = Path("/var/lib/bristolbusbot/monitoring/daily-export.json")
REASON_RE = re.compile(r"^[a-z][a-z_]{0,39}$")
BRANCH_RE = re.compile(r"^(readings|rare-workings|writer-drafts)/(\d{4}-\d{2}-\d{2})")


class PartError(RuntimeError):
    """A part failed for a known reason. ``code`` is a short safe reason code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code if REASON_RE.fullmatch(code) else "unexpected_error"


# ---------------------------------------------------------------------------
# Readings (audit.db)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DayTable:
    name: str
    columns: tuple[str, ...]
    required: bool = True
    by_poll_time: bool = False          # poll_log is keyed by UTC time, not service date


READING_TABLES = (
    DayTable("timepoint_observations", (
        "service_date", "operator", "route", "trip_id", "siri_journey_ref", "stop_sequence",
        "stop_code", "scheduled_local", "observed_delay_s", "on_time", "gps_distance_m",
        "recorded_at", "vehicle_ref", "is_origin", "match_tier", "is_final")),
    DayTable("expected_trips", (
        "service_date", "operator", "route", "trip_id", "siri_ref", "direction",
        "first_departure", "route_id", "service_id", "block_id", "vehicle_journey_code",
        "first_stop_id", "first_stop_code", "timetable_edition", "last_departure")),
    DayTable("daily_trip_coverage_days", (
        "service_date", "is_valid", "invalid_reasons_json", "poll_window_start",
        "poll_window_end", "expected_polls", "recorded_polls", "successful_polls",
        "successful_poll_rate_pct", "successful_poll_coverage_pct",
        "max_successful_poll_gap_s", "candidate_readings", "matched_readings",
        "match_rate_pct", "scheduled_trips", "observed_trips", "unobserved_trips",
        "exact_observed_trips", "fuzzy_observed_trips", "unknown_tier_observed_trips",
        "invalid_departure_times")),
    DayTable("daily_trip_coverage", (
        "service_date", "operator", "route", "direction", "time_band", "scheduled_trips",
        "observed_trips", "unobserved_trips", "exact_observed_trips",
        "fuzzy_observed_trips", "unknown_tier_observed_trips")),
    DayTable("daily_overall_summary", (
        "service_date", "operator", "readings_in_gate", "on_time", "early", "late",
        "on_time_pct", "mean_delay_s", "median_delay_s", "readings_total",
        "excluded_distance", "median_gate_dist_m", "expected_trips", "observed_trips",
        "coverage_pct"), required=False),
    DayTable("daily_route_summary", (
        "service_date", "operator", "route", "readings_in_gate", "on_time", "early", "late",
        "on_time_pct", "mean_delay_s", "median_delay_s", "readings_total",
        "excluded_distance", "median_gate_dist_m", "expected_trips", "observed_trips",
        "coverage_pct"), required=False),
    DayTable("daily_duty_gap_days", (
        "service_date", "operator", "is_valid", "invalid_reasons_json", "scheduled_trips",
        "duty_detail_trips", "duty_detail_pct", "scheduled_blocks", "usable_blocks",
        "missing_middle_trips", "same_vehicle_gaps", "short_connection_gaps",
        "ambiguous_match_gaps", "strict_candidate_gaps"), required=False),
    DayTable("daily_duty_gap_candidates", (
        "service_date", "operator", "block_id", "trip_id", "route", "direction",
        "first_departure", "last_departure", "vehicle_ref", "previous_trip_id",
        "previous_route", "previous_departure", "previous_siri_ref", "next_trip_id",
        "next_route", "next_departure", "next_siri_ref", "connection_before_s",
        "connection_after_s", "previous_match_window", "next_match_window"), required=False),
    DayTable("daily_fleet_summary", (
        "service_date", "operator", "model", "electric", "fuel", "vehicles",
        "readings_in_gate", "on_time", "on_time_pct", "mean_delay_s", "median_delay_s"),
        required=False),
    DayTable("matching_evidence", (
        "evidence_id", "captured_at", "service_date", "reasons_json",
        "calculation_reasons_json", "operator", "route", "vehicle_ref", "direction",
        "journey_ref", "origin_aimed_departure", "recorded_at", "lat", "lon", "bearing",
        "block_ref", "chosen_trip_id", "match_tier", "candidate_count",
        "candidates_truncated", "gps_distance_m", "delay_s", "event_type",
        "timetable_route_id", "timetable_service_id", "timetable_direction_id",
        "timetable_edition", "alternatives_json", "origin_ref", "destination_ref"),
        required=False),
    DayTable("poll_log", (
        "poll_at", "ok", "vehicles_total", "candidates", "matched", "obs_written",
        "dropped_insane", "stale", "evidence_written", "evidence_dropped",
        "evidence_deduplicated", "evidence_scope_dropped", "evidence_quota_dropped",
        "evidence_errors"), by_poll_time=True),
)


def open_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise PartError("source_missing", str(path))
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=60)
    connection.execute("PRAGMA busy_timeout=60000")
    connection.execute("PRAGMA query_only=ON")
    return connection


def day_query(table: DayTable, present: list[str]) -> str:
    selected = ", ".join(f'"{name}"' for name in present)
    if table.by_poll_time:
        return f'SELECT {selected} FROM "{table.name}" WHERE poll_at >= ? AND poll_at < ?'
    return f'SELECT {selected} FROM "{table.name}" WHERE service_date = ?'


def day_parameters(table: DayTable, day: date) -> tuple[str, ...]:
    if table.by_poll_time:
        return (day.isoformat(), (day + timedelta(days=1)).isoformat())
    return (day.strftime("%Y%m%d"),)


def uses_index(connection: sqlite3.Connection, query: str, parameters: tuple) -> bool:
    plan = " ".join(str(row[-1]) for row in connection.execute(
        "EXPLAIN QUERY PLAN " + query, parameters))
    return "SEARCH" in plan and "SCAN" not in plan


def export_readings(audit_db: Path, day: date, out_dir: Path) -> tuple[Path, dict]:
    source = open_read_only(audit_db)
    work = out_dir / f"readings-{day.isoformat()}.sqlite"
    work.unlink(missing_ok=True)
    destination = sqlite3.connect(work)
    counts: dict[str, int] = {}
    try:
        tables = {row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for table in READING_TABLES:
            if table.name not in tables:
                if table.required:
                    raise PartError("source_table_missing", table.name)
                continue
            available = {row[1] for row in source.execute(f'PRAGMA table_info("{table.name}")')}
            present = [name for name in table.columns if name in available]
            query, parameters = day_query(table, present), day_parameters(table, day)
            if not uses_index(source, query, parameters):
                # Never scan a whole table on the Pi; skip rather than risk it.
                raise PartError("query_would_scan", table.name)
            destination.execute(
                f'CREATE TABLE "{table.name}" ({", ".join(chr(34) + n + chr(34) for n in present)})')
            insert = (f'INSERT INTO "{table.name}" VALUES '
                      f'({", ".join("?" * len(present))})')
            cursor = source.execute(query, parameters)
            count = 0
            while rows := cursor.fetchmany(5000):
                destination.executemany(insert, rows)
                count += len(rows)
            counts[table.name] = count
        destination.execute("CREATE TABLE export_info (key TEXT PRIMARY KEY, value TEXT)")
        destination.executemany("INSERT INTO export_info VALUES (?, ?)", [
            ("schema_version", str(SCHEMA_VERSION)),
            ("service_date", day.strftime("%Y%m%d")),
            ("generated_at", datetime.now(timezone.utc).isoformat()),
            ("row_counts", json.dumps(counts, sort_keys=True)),
            ("poll_log_window", "UTC calendar day of the service date"),
        ])
        destination.commit()
    except sqlite3.Error as exc:
        raise PartError("source_unreadable", str(exc)) from exc
    finally:
        destination.close()
        source.close()
    scan_file_text(iter_sqlite_text(work))
    archive = gzip_file(work)
    return archive, {"rows": counts.get("timepoint_observations", 0), "tables": counts}


def iter_sqlite_text(path: Path) -> Iterable[str]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            for row in connection.execute(f'SELECT * FROM "{name}"'):
                yield " ".join(str(value) for value in row if isinstance(value, str))
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Rare-working shadow observations (bot app_data.db)
# ---------------------------------------------------------------------------
def london_day_bounds_utc(day: date) -> tuple[str, str]:
    start = datetime.combine(day, datetime.min.time(), LONDON).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), LONDON).astimezone(timezone.utc)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def shadow_first_day(app_db: Path) -> date | None:
    try:
        connection = open_read_only(app_db)
    except PartError:
        return None
    try:
        row = connection.execute("SELECT MIN(observed_at) FROM rare_working_shadow_seen").fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if not row or not row[0]:
        return None
    return datetime.fromisoformat(row[0].replace("Z", "+00:00")).astimezone(LONDON).date()


def export_rare_workings(app_db: Path, day: date, out_dir: Path) -> tuple[Path, dict]:
    source = open_read_only(app_db)
    start, end = london_day_bounds_utc(day)
    try:
        exists = source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rare_working_shadow_seen'"
        ).fetchone()
        # The table holds a handful of rows a day (one per accepted observation),
        # so reading it by time is small even without an index on observed_at.
        rows = source.execute(
            "SELECT event_id, observed_at, payload_json FROM rare_working_shadow_seen "
            "WHERE observed_at >= ? AND observed_at < ? ORDER BY observed_at, event_id",
            (start, end)).fetchall() if exists else []
    except sqlite3.Error as exc:
        raise PartError("source_unreadable", str(exc)) from exc
    finally:
        source.close()
    lines = []
    for event_id, observed_at, payload in rows:
        try:
            parsed = json.loads(payload)
        except (TypeError, ValueError):
            parsed = {"unparsed_payload": payload}
        lines.append(json.dumps({"event_id": event_id, "observed_at": observed_at,
                                 "observation": parsed}, sort_keys=True))
    scan_file_text(lines)
    path = out_dir / f"rare-workings-{day.isoformat()}.jsonl.gz"
    write_gzip_lines(path, lines)
    return path, {"rows": len(lines), "table_present": bool(exists)}


# ---------------------------------------------------------------------------
# Writer training drafts (bot JSONL, rotated to ".1" at 25 MB)
# ---------------------------------------------------------------------------
def read_new_draft_lines(training: Path, cursor: dict | None) -> tuple[list[str], dict, dict]:
    """New complete lines since ``cursor``; returns (lines, new cursor, notes)."""
    rotated = Path(f"{training}.1")
    notes: dict = {}
    segments: list[tuple[Path, int]] = []    # (file, start offset)

    def inode(path: Path) -> int | None:
        try:
            return path.stat().st_ino
        except OSError:
            return None

    current_ino, rotated_ino = inode(training), inode(rotated)
    if not cursor:
        notes["first_run"] = True
        if rotated_ino is not None:
            segments.append((rotated, 0))
        if current_ino is not None:
            segments.append((training, 0))
    elif current_ino is not None and current_ino == cursor.get("inode"):
        segments.append((training, int(cursor.get("offset") or 0)))
    else:
        # The file rotated since the last run: finish the old one, then the new.
        if rotated_ino is not None and rotated_ino == cursor.get("inode"):
            segments.append((rotated, int(cursor.get("offset") or 0)))
        elif rotated_ino is not None:
            notes["lines_may_be_missing"] = True   # rotated twice between runs
            segments.append((rotated, 0))
        if current_ino is not None:
            segments.append((training, 0))

    lines: list[str] = []
    new_cursor = dict(cursor or {})
    for path, start in segments:
        try:
            with path.open("rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                if start > size:            # truncated or replaced: start again
                    start = 0
                handle.seek(start)
                data = handle.read()
                inode_now = os.fstat(handle.fileno()).st_ino
        except OSError as exc:
            raise PartError("source_unreadable", str(exc)) from exc
        complete = data[:data.rfind(b"\n") + 1] if b"\n" in data else b""
        for raw in complete.splitlines():
            text = raw.decode("utf-8", errors="replace").strip()
            if text:
                lines.append(text)
        if path == training:
            new_cursor = {"inode": inode_now, "offset": start + len(complete)}
    if not segments:
        notes["no_file_yet"] = True
    return lines, new_cursor, notes


def export_writer_drafts(training: Path, cursor: dict | None, out_dir: Path,
                         run_label: str) -> tuple[Path, dict, dict]:
    lines, new_cursor, notes = read_new_draft_lines(training, cursor)
    scan_file_text(lines)
    path = out_dir / f"writer-drafts-{run_label}.jsonl.gz"
    write_gzip_lines(path, lines)
    return path, new_cursor, {"rows": len(lines), **notes}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def scan_file_text(texts: Iterable[str]) -> None:
    """The repository's secret scan, run on everything that leaves the Pi."""
    for text in texts:
        for _label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                raise PartError("secret_scan_finding")


def gzip_file(path: Path) -> Path:
    target = path.with_name(path.name + ".gz")
    with path.open("rb") as source, gzip.open(target, "wb", compresslevel=9) as out:
        shutil.copyfileobj(source, out)
    path.unlink()
    return target


def write_gzip_lines(path: Path, lines: list[str]) -> None:
    with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as handle:
        for line in lines:
            handle.write(line + "\n")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def days_to_export(done: set[str], first: date, yesterday: date, per_night: int) -> list[date]:
    """Yesterday first, then the oldest missing days, at most ``per_night`` in all."""
    oldest_kept = yesterday - timedelta(days=RETENTION_DAYS - 1)
    wanted = []
    day = max(first, oldest_kept)
    while day <= yesterday:
        if day.isoformat() not in done:
            wanted.append(day)
        day += timedelta(days=1)
    if yesterday in wanted:
        wanted.remove(yesterday)
        wanted.insert(0, yesterday)
    return wanted[:per_night]


# ---------------------------------------------------------------------------
# The store: a private GitHub repository, one branch per file
# ---------------------------------------------------------------------------
class GitStore:
    """Pushes each file as its own single-commit branch; no clone is kept.

    One branch per file keeps the Pi free of a growing repository (git gc is
    off on the Pi), lets each part push on its own, and makes retention a
    branch delete.
    """

    def __init__(self, remote: str, key: Path | None, runner: Callable = subprocess.run):
        self.remote = remote
        self.runner = runner
        self.env = {**os.environ, "GIT_TERMINAL_PROMPT": "0",
                    "GIT_AUTHOR_NAME": "Bristol Bus Bot Pi",
                    "GIT_AUTHOR_EMAIL": "pi@bristolbusbot.invalid",
                    "GIT_COMMITTER_NAME": "Bristol Bus Bot Pi",
                    "GIT_COMMITTER_EMAIL": "pi@bristolbusbot.invalid"}
        if key:
            self.env["GIT_SSH_COMMAND"] = (
                f"ssh -i {key} -o IdentitiesOnly=yes -o BatchMode=yes "
                "-o StrictHostKeyChecking=yes -o UpdateHostKeys=no")

    def git(self, *args: str, cwd: Path | None = None, timeout: int = 300) -> str:
        try:
            result = self.runner(["git", "-c", "gc.auto=0", *args], cwd=cwd, env=self.env,
                                 capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PartError("push_failed", type(exc).__name__) from exc
        if result.returncode != 0:
            raise PartError("push_failed", (result.stderr or "")[-300:])
        return result.stdout

    def push(self, branch: str, path_in_repo: str, file: Path, message: str) -> None:
        with tempfile.TemporaryDirectory(prefix="bbb-daily-export-") as work:
            repo = Path(work)
            self.git("init", "-q", "-b", "main", str(repo))
            target = repo / path_in_repo
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
            self.git("add", path_in_repo, cwd=repo)
            self.git("commit", "-q", "-m", message, cwd=repo)
            self.git("push", "-q", "--force", self.remote, f"HEAD:refs/heads/{branch}", cwd=repo)

    def branches(self) -> list[str]:
        out = self.git("ls-remote", "--heads", self.remote)
        return [line.split("refs/heads/", 1)[1] for line in out.splitlines() if "refs/heads/" in line]

    def delete(self, branches: list[str]) -> None:
        if branches:
            with tempfile.TemporaryDirectory(prefix="bbb-daily-export-") as work:
                self.git("init", "-q", work)
                self.git("push", "-q", self.remote, *[f":refs/heads/{b}" for b in branches], cwd=Path(work))


def expired_branches(branches: list[str], today: date) -> list[str]:
    cutoff = today - timedelta(days=RETENTION_DAYS)
    expired = []
    for branch in branches:
        match = BRANCH_RE.match(branch)
        if match and date.fromisoformat(match.group(2)) < cutoff:
            expired.append(branch)
    return sorted(expired)


# ---------------------------------------------------------------------------
# The nightly run
# ---------------------------------------------------------------------------
def run(*, audit_db: Path, app_db: Path, training: Path, state_dir: Path, status_path: Path,
        store: GitStore | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(LONDON).date()
    yesterday = today - timedelta(days=1)
    state_path = state_dir / "state.json"
    state = load_json(state_path)
    out_dir = state_dir / "outgoing"
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: dict[str, dict] = {}

    def ship(part: str, file: Path, label: str) -> int:
        size = file.stat().st_size
        if size > MAX_PART_BYTES:
            raise PartError("file_too_large", str(size))
        if store is not None:
            suffix = "".join(file.suffixes[-2:])
            store.push(f"{part}/{label}", f"{part}/{label}{suffix}", file,
                       f"{part} {label}")
        return size

    # Readings and rare workings: one file per day, catch-up included.
    for part, first, exporter, source in (
            ("readings", CATCH_UP_FROM, export_readings, audit_db),
            ("rare-workings", None, export_rare_workings, app_db)):
        done = set(state.get(part) or [])
        result: dict = {"ok": True, "days": [], "rows": 0, "bytes": 0}
        if first is None:
            first = shadow_first_day(app_db) or yesterday
        try:
            for day in days_to_export(done, first, yesterday, CATCH_UP_PER_NIGHT[part]):
                file, counts = exporter(source, day, out_dir)
                result["bytes"] += ship(part, file, day.isoformat())
                file.unlink(missing_ok=True)
                done.add(day.isoformat())
                result["days"].append(day.isoformat())
                result["rows"] += counts["rows"]
        except PartError as exc:
            result.update(ok=False, reason=exc.code)
        except Exception as exc:  # noqa: BLE001 - one part must never stop the others
            result.update(ok=False, reason="unexpected_error", error=type(exc).__name__)
        oldest = (yesterday - timedelta(days=RETENTION_DAYS - 1)).isoformat()
        state[part] = sorted(d for d in done if d >= oldest)
        result["days_left"] = len(days_to_export(
            set(state[part]), first, yesterday, 10_000))
        parts[part] = result
        write_json(state_path, state)

    # Writer drafts: everything new since the last successful export.
    result = {"ok": True}
    label = now.strftime("%Y-%m-%dT%H%MZ")
    try:
        file, cursor, counts = export_writer_drafts(
            training, state.get("writer-drafts"), out_dir, label)
        result.update(counts)
        if counts["rows"]:
            result["bytes"] = ship("writer-drafts", file, label)
            result["label"] = label
        file.unlink(missing_ok=True)
        state["writer-drafts"] = cursor     # advance only after a successful push
    except PartError as exc:
        result.update(ok=False, reason=exc.code)
    except Exception as exc:  # noqa: BLE001
        result.update(ok=False, reason="unexpected_error", error=type(exc).__name__)
    parts["writer-drafts"] = result
    write_json(state_path, state)

    retention: dict = {"ok": True, "deleted": 0}
    if store is not None:
        try:
            expired = expired_branches(store.branches(), today)
            store.delete(expired)
            retention["deleted"] = len(expired)
        except PartError as exc:
            retention.update(ok=False, reason=exc.code)
    status = {"schema": 1, "finished_at": datetime.now(timezone.utc).isoformat(),
              "target_day": yesterday.isoformat(), "pushed": store is not None,
              "parts": parts, "retention": retention}
    write_json(status_path, status)
    return status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB)
    parser.add_argument("--app-db", type=Path, default=DEFAULT_APP_DB)
    parser.add_argument("--training", type=Path, default=DEFAULT_TRAINING)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--no-push", action="store_true", help="build the files only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    store = None
    if not args.no_push:
        remote = os.environ.get("BBB_DAILY_EXPORT_REMOTE", "").strip()
        key = os.environ.get("BBB_DAILY_EXPORT_KEY", "").strip()
        if not remote or (key and not Path(key).is_file()):
            write_json(args.status, {
                "schema": 1, "finished_at": datetime.now(timezone.utc).isoformat(),
                "pushed": False, "retention": {"ok": False, "reason": "store_not_configured"},
                "parts": {part: {"ok": False, "reason": "store_not_configured"} for part in PARTS}})
            write_reason("store_not_configured")
            print("the private store is not set up yet (remote or deploy key missing)",
                  file=sys.stderr)
            return 1
        store = GitStore(remote, Path(key) if key else None)
    started = time.monotonic()
    status = run(audit_db=args.audit_db, app_db=args.app_db, training=args.training,
                 state_dir=args.state_dir, status_path=args.status, store=store)
    failed = [name for name, part in status["parts"].items() if not part.get("ok")]
    for name, part in status["parts"].items():
        print(f"{name}: {'ok' if part.get('ok') else 'FAILED ' + part.get('reason', '')} "
              f"days={part.get('days', part.get('label'))} rows={part.get('rows')} "
              f"bytes={part.get('bytes')}")
    print(f"finished in {time.monotonic() - started:.1f}s")
    if failed:
        write_reason("part_failed")
        return 1
    return 0


def write_reason(code: str) -> None:
    target = os.environ.get("BBB_JOB_REASON_FILE")
    if target and REASON_RE.fullmatch(code):
        try:
            Path(target).write_text(code + "\n", encoding="utf-8")
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

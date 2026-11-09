"""The collector's main loop: fetch -> filter -> match -> measure -> write.

Everything hard is in the tested modules; this file is assembly. The cycle
functions take an injected `fetch` callable so tests drive them with canned
XML and no network.

Run:  python -m collector.run   (from collector/, with .env + timetable.db)
"""
from __future__ import annotations

import logging
import json
import signal
import sqlite3
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import xmltodict

from . import audit_db, live_db
from .clock_change import ClockChangeTrips
from .config import Config
from .delay import (classify_event, closest_stop, live_estimate_result,
                    settled_reading_result, ON_TIME_HIGH_S, ON_TIME_LOW_S,
                    SANITY_MAX_S, SANITY_MIN_S)
from .geo import BoundaryFilter
from .matching import explain_match, match_vehicle
from .siri import (activities_from_xmltodict, anchor_departure_local,
                   clean_destination, extract_snapshot)
from .sirisx import in_scope, parse_situations
from .secret_filter import install_query_secret_filter, redact_query_secrets
from .systemd_watchdog import notify_watchdog
from .timeparse import gtfs_seconds, scheduled_local, service_midnight

logger = logging.getLogger("collector")

SIRI_VM_URL = "https://data.bus-data.dft.gov.uk/api/v1/datafeed/"
SIRI_SX_URL = "https://data.bus-data.dft.gov.uk/api/v1/siri-sx/"
EVIDENCE_EXTREME_LATE_S = 45 * 60
EVIDENCE_EXTREME_EARLY_S = -10 * 60
CLOCK_CHANGE_TRIPS = ClockChangeTrips()


def _shifted(moment: datetime, seconds: int) -> datetime:
    return (moment.astimezone(timezone.utc)
            + timedelta(seconds=seconds)).astimezone(moment.tzinfo)


class AuditBacklog:
    """audit.db writes from polls that found the database busy.

    Readings and poll records are kept in poll order and written at the start
    of the next poll that gets the lock. Replaying readings late is safe
    because upsert_observation keeps the closest reading per trip and stop.
    The cap stops a lock that never clears from filling the Pi's memory;
    the oldest entries go first.
    """

    MAX_ITEMS = 5000

    def __init__(self) -> None:
        self.items: list[tuple] = []
        self.dropped = 0

    def extend(self, items: list[tuple]) -> None:
        self.items.extend(items)
        overflow = len(self.items) - self.MAX_ITEMS
        if overflow > 0:
            del self.items[:overflow]
            self.dropped += overflow
            logger.warning("audit backlog full: dropped %s oldest held writes",
                           overflow)


def _is_busy(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and (
        "locked" in str(exc) or "busy" in str(exc))


def _write_audit(audit_conn, backlog: AuditBacklog, ops: list[tuple],
                 evidence: list[dict], poll_at_iso: str, ok: bool,
                 counters: dict, target_tz) -> bool:
    """Write one poll's audit rows after live.db has been committed.

    Everything goes in one transaction. Only its first write can find the
    database busy; once it holds the lock, later statements do not wait. So a
    busy database costs at most one busy-timeout wait per poll. Returns False
    when the rows were held back for the next poll.
    """
    held = backlog.items
    try:
        cur = audit_conn.cursor()
        for item in held + ops:
            _apply_audit_op(cur, audit_conn, item, target_tz)
        for record in evidence:
            _write_evidence(audit_conn, record, counters)
        audit_db.log_poll(audit_conn, poll_at_iso, ok, counters)
        audit_conn.commit()
    except sqlite3.OperationalError as exc:
        if not _is_busy(exc):
            raise
        # Close the implicit transaction now; a stale read view would make
        # later writes on this connection fail at once without waiting.
        audit_conn.rollback()
        # Matching evidence is a debugging sample: drop it rather than hold it.
        counters["evidence_dropped"] += len(evidence)
        counters["evidence_errors"] += len(evidence)
        backlog.items = held
        backlog.extend([*ops, ("poll", poll_at_iso, ok, dict(counters))])
        logger.warning(
            "audit.db busy (%s): holding %s writes for the next poll", exc,
            len(backlog.items))
        return False
    if held:
        logger.info("audit.db: wrote %s held writes", len(held))
    backlog.items = []
    return True


def _apply_audit_op(cur, conn, item, target_tz) -> None:
    kind = item[0]
    if kind == "obs":
        audit_db.upsert_observation(cur, item[1])
    elif kind == "clock":
        audit_db.apply_clock_reading(cur, *item[1:], target_tz)
    elif kind == "poll":
        audit_db.log_poll(conn, item[1], item[2], item[3])
    else:  # pragma: no cover - programming error
        raise ValueError(f"unknown audit write {kind!r}")


def _write_evidence(audit_conn, record: dict, counters: dict) -> None:
    """Store one evidence receipt. Only a busy database escapes to the caller."""
    audit_conn.execute("SAVEPOINT evidence")
    try:
        status = audit_db.write_matching_evidence(audit_conn, record)
    except Exception as exc:
        audit_conn.execute("ROLLBACK TO evidence")
        audit_conn.execute("RELEASE evidence")
        if _is_busy(exc):
            raise
        # Debug evidence must never interrupt the live collector.
        counters["evidence_dropped"] += 1
        counters["evidence_errors"] += 1
        logger.exception("could not save matching evidence for vehicle %s",
                         record.get("vehicle_ref"))
        return
    audit_conn.execute("RELEASE evidence")
    if status in {"written", "total_pruned"}:
        counters["evidence_written"] += 1
    elif status in {"duplicate", "journey_duplicate"}:
        counters["evidence_deduplicated"] += 1
    elif status == "operator_out_of_scope":
        counters["evidence_dropped"] += 1
        counters["evidence_scope_dropped"] += 1
    elif status in {"daily_limit", "cell_limit", "reserved_slot",
                    "operator_cell_limit"}:
        counters["evidence_dropped"] += 1
        counters["evidence_quota_dropped"] += 1
    else:
        counters["evidence_dropped"] += 1
        counters["evidence_errors"] += 1


def _apply_clock_change(clock_trips, service_date, match, est, reading,
                        estimate_reason, reading_reason, audit_ops, target_tz):
    """Resolve change-night readings to the standard or the clock reading.

    See clock_change. Off change nights every offset is zero and nothing
    changes. Returns (est, reading, estimate_reason, reading_reason).
    """
    # A bus standing at its first stop cannot show which reading it runs to.
    origin_seq = int(match.schedule[0][0])
    probe = None
    if est is not None and est.clock_offset_s \
            and est.stop_sequence != origin_seq:
        probe = (est.delay_s, est.clock_offset_s)
    elif reading is not None and reading.clock_offset_s \
            and not reading.is_origin:
        probe = (reading.observed_delay_s, reading.clock_offset_s)
    before = clock_trips.decision(service_date, match.trip_id)
    decision = (clock_trips.observe(service_date, match.trip_id, *probe)
                if probe else before)
    if decision == "clock" and before != "clock":
        audit_ops.append(("clock", service_date, match.trip_id,
                          list(match.schedule)))
    if est is not None and est.clock_offset_s:
        shift = est.clock_offset_s if decision == "clock" else 0
        delay = est.delay_s - shift
        if decision == "pending":
            est, estimate_reason = None, "clock_change_pending"
        elif not SANITY_MIN_S <= delay <= SANITY_MAX_S:
            est, estimate_reason = None, "sanity_rejected"
        elif shift:
            est = replace(est, delay_s=delay, event_type=classify_event(delay),
                          scheduled_local=_shifted(est.scheduled_local, shift))
    if reading is not None and reading.clock_offset_s:
        shift = reading.clock_offset_s if decision == "clock" else 0
        delay = reading.observed_delay_s - shift
        if not SANITY_MIN_S <= delay <= SANITY_MAX_S:
            reading, reading_reason = None, "sanity_rejected"
        elif shift:
            reading = replace(
                reading, observed_delay_s=delay,
                on_time=ON_TIME_LOW_S <= delay <= ON_TIME_HIGH_S,
                scheduled_local=_shifted(reading.scheduled_local, shift))
    return est, reading, estimate_reason, reading_reason


def _journey_within_age_limit(match, origin_local, now_utc, max_age_h):
    """Let a valid timetable's finish and late allowance extend the age limit.

    The existing late-reading ceiling bounds the extension after its end.
    This does not replace the independent recorded-position freshness gate.
    """
    max_age = timedelta(hours=max_age_h)
    if now_utc - origin_local.astimezone(timezone.utc) <= max_age:
        return True
    times = [gtfs_seconds(row[1]) for row in match.schedule]
    if (len(times) < 2 or any(value is None for value in times)
            or any(b < a for a, b in zip(times, times[1:]))):
        return False
    midnight = service_midnight(origin_local, times[0])
    end = scheduled_local(midnight, times[-1]).astimezone(timezone.utc)
    return now_utc <= end + timedelta(seconds=SANITY_MAX_S)


def _measurement_clues(snap, match, service_midnight_dt, est, reading) -> dict:
    """Return the useful numbers even when the normal sanity gate rejected."""
    if est is not None:
        return {
            "gps_distance_m": est.distance_m,
            "delay_s": est.delay_s,
            "event_type": est.event_type,
        }
    if reading is not None:
        delay_s = reading.observed_delay_s
        return {
            "gps_distance_m": reading.gps_distance_m,
            "delay_s": delay_s,
            "event_type": classify_event(delay_s),
        }
    if service_midnight_dt is None:
        return {}
    nearest = closest_stop(
        snap.lat, snap.lon, match.schedule, snap.recorded_utc,
        service_midnight_dt)
    if nearest is None:
        return {}
    stop_secs = gtfs_seconds(nearest.row[1])
    if stop_secs is None:
        return {"gps_distance_m": int(round(nearest.distance_m))}
    expected = scheduled_local(service_midnight_dt, stop_secs)
    delay_s = int(round((
        snap.recorded_utc - expected.astimezone(timezone.utc)).total_seconds()))
    return {
        "gps_distance_m": int(round(nearest.distance_m)),
        "delay_s": delay_s,
        "event_type": classify_event(delay_s),
    }


CONNECT_TIMEOUT_S = 5


def make_session() -> requests.Session:
    """One attempt per fetch, no retries inside a poll.

    The main loop already tries again 30 s later. Retrying inside one poll
    (with back-off and Retry-After) let a single slow fetch run past 90 s,
    and with SIRI-SX in the same pass that outlasted the 120 s watchdog
    (6 Nov 2026).
    """
    from requests.adapters import HTTPAdapter
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=0))
    return s


def _timeout(cfg: Config) -> tuple[float, float]:
    """Connect timeout, then the per-read timeout from BBB_FETCH_TIMEOUT_S."""
    return (CONNECT_TIMEOUT_S, cfg.fetch_timeout_s)


def fetch_vm(session: requests.Session, cfg: Config) -> str | None:
    try:
        r = session.get(
            f"{SIRI_VM_URL}?boundingBox={quote(cfg.bounding_box)}&api_key={cfg.bods_api_key}",
            timeout=_timeout(cfg))
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        logger.warning("SIRI-VM fetch failed: %s", redact_query_secrets(e))
        return None


def fetch_sx(session: requests.Session, cfg: Config) -> bytes | None:
    try:
        r = session.get(f"{SIRI_SX_URL}?api_key={cfg.bods_api_key}",
                        timeout=_timeout(cfg))
        r.raise_for_status()
        return r.content
    except requests.RequestException as e:
        logger.warning("SIRI-SX fetch failed: %s", redact_query_secrets(e))
        return None


def vm_cycle(fetch, tt_cur, live_conn, audit_conn, boundary: BoundaryFilter,
             cfg: Config, target_tz, now_utc: datetime | None = None,
             clock_trips: ClockChangeTrips | None = None,
             backlog: AuditBacklog | None = None) -> dict:
    """One SIRI-VM poll. Returns counters (also written to both DBs).

    live.db is committed first, so the map updates even when audit.db is busy.
    audit.db rows are collected during the loop and written afterwards; a busy
    audit.db holds them in `backlog` for the next poll (`audit_written` False).
    `now_utc` is injectable so tests are independent of the wall clock."""
    poll_at = now_utc or datetime.now(timezone.utc)
    clock_trips = clock_trips or CLOCK_CHANGE_TRIPS
    backlog = backlog if backlog is not None else AuditBacklog()
    audit_ops: list[tuple] = []
    evidence: list[dict] = []
    xml = fetch()
    counters = {"vehicles_total": 0, "candidates": 0, "matched": 0,
                "obs_written": 0, "dropped_insane": 0, "events": 0,
                "stale": 0, "evidence_written": 0,
                "evidence_dropped": 0, "evidence_deduplicated": 0,
                "evidence_scope_dropped": 0,
                "evidence_quota_dropped": 0, "evidence_errors": 0}

    if xml is None:
        live_db.record_poll(live_conn, "siri_vm", ok=False)
        live_conn.commit()
        written = _write_audit(audit_conn, backlog, [], [], poll_at.isoformat(),
                               False, counters, target_tz)
        return {"ok": False, "audit_written": written, **counters}

    acts = activities_from_xmltodict(xmltodict.parse(xml))
    counters["vehicles_total"] = len(acts)
    now_utc = now_utc or datetime.now(timezone.utc)
    now_local = now_utc.astimezone(target_tz)

    for act in acts:
        snap = extract_snapshot(act)
        if snap is None:
            continue
        # BODS re-broadcasts laid-over/parked vehicles with RecordedAtTime
        # minutes-to-hours old. A stale position is a ghost: writing it
        # keeps the vehicle alive on the map (updated_at refreshes every
        # cycle) while it sits frozen at a stand. Skip; the row it already
        # has ages out via the site's updated_at cutoff.
        if (now_utc - snap.recorded_utc).total_seconds() > cfg.max_recorded_age_s:
            counters["stale"] += 1
            continue
        if not boundary.contains(snap.lat, snap.lon):
            continue
        counters["candidates"] += 1

        mvj = act.get("MonitoredVehicleJourney") or act.get("siri:MonitoredVehicleJourney")
        origin_local, _src = anchor_departure_local(mvj, target_tz, now_local)
        match = None
        est = None
        reading = None
        service_midnight_dt = None
        estimate_reason = None
        reading_reason = None
        service_date = snap.recorded_utc.astimezone(target_tz).strftime("%Y%m%d")
        if origin_local is not None:
            match = match_vehicle(tt_cur, snap.operator_ref, snap.line,
                                  snap.direction, origin_local,
                                  snap.journey_ref, cfg.enable_exact_match,
                                  vehicle_pos=(snap.lat, snap.lon),
                                  origin_ref=snap.origin_stop_ref,
                                  destination_ref=snap.destination_stop_ref)
            if match and not _journey_within_age_limit(
                    match, origin_local, now_utc, cfg.max_journey_age_h):
                match = None
        if match:
            counters["matched"] += 1
            first_secs = gtfs_seconds(match.schedule[0][1])
            if first_secs is not None:
                service_midnight_dt = service_midnight(origin_local, first_secs)
                service_date = service_midnight_dt.strftime("%Y%m%d")
                est, estimate_reason = live_estimate_result(
                    snap.lat, snap.lon, snap.recorded_utc, match.schedule,
                    service_midnight_dt)
                reading, reading_reason = settled_reading_result(
                    snap.lat, snap.lon, snap.recorded_utc, match.schedule,
                    service_midnight_dt)
                est, reading, estimate_reason, reading_reason =                     _apply_clock_change(
                        clock_trips, service_date, match, est, reading,
                        estimate_reason, reading_reason, audit_ops,
                        target_tz)
                if "sanity_rejected" in {estimate_reason, reading_reason}:
                    # One matched vehicle is counted once even when both the
                    # live and audit calculations reject the same timestamp.
                    counters["dropped_insane"] += 1
                if reading is not None:
                    audit_ops.append(("obs", (
                        service_date, snap.operator_ref,
                        match.route_short_name, match.trip_id, snap.journey_ref,
                        reading.stop_sequence, reading.stop_code,
                        reading.scheduled_local.isoformat(),
                        reading.observed_delay_s, int(reading.on_time),
                        reading.gps_distance_m, snap.recorded_utc.isoformat(),
                        snap.vehicle_ref, int(reading.is_origin), match.tier)))
                    counters["obs_written"] += 1

        decision = live_db.upsert_vehicle(live_conn, snap, est, match,
                                          destination=clean_destination(snap.destination_raw))
        if decision.emit:
            counters["events"] += 1

        evidence_reasons = set()
        if reading_reason == "ambiguous_stop_visit":
            evidence_reasons.add("ambiguous_stop_visit")
        if "sanity_rejected" in {estimate_reason, reading_reason}:
            evidence_reasons.add("sanity_rejected")
        if est is not None and (
                est.delay_s >= EVIDENCE_EXTREME_LATE_S
                or est.delay_s <= EVIDENCE_EXTREME_EARLY_S):
            evidence_reasons.add("extreme_delay")
        if decision.same_journey_run and match is not None:
            if decision.previous_trip_id and \
                    decision.previous_trip_id != match.trip_id:
                evidence_reasons.add("match_changed_within_run")
            previous_direction = (decision.previous_direction or "").strip().lower()
            current_direction = (snap.direction or "").strip().lower()
            if previous_direction and current_direction \
                    and previous_direction != current_direction:
                evidence_reasons.add("direction_changed_within_run")

        if evidence_reasons and match is not None and origin_local is not None:
            try:
                if snap.operator_ref not in audit_db.MATCHING_EVIDENCE_OPERATORS:
                    counters["evidence_dropped"] += 1
                    counters["evidence_scope_dropped"] += 1
                    continue
                explanation = explain_match(
                    tt_cur, match, snap.operator_ref, snap.line, snap.direction,
                    origin_local, (snap.lat, snap.lon), service_date)
                clues = _measurement_clues(
                    snap, match, service_midnight_dt, est, reading)
                evidence.append({
                    "captured_at": poll_at.isoformat(),
                    "service_date": service_date,
                    "reasons": evidence_reasons,
                    "calculation_reasons": {
                        reason for reason in (estimate_reason, reading_reason)
                        if reason
                    },
                    "operator": snap.operator_ref,
                    "route": match.route_short_name,
                    "vehicle_ref": snap.vehicle_ref,
                    "direction": snap.direction,
                    "journey_ref": snap.journey_ref,
                    "origin_aimed_departure": snap.origin_aimed_departure,
                    "origin_ref": snap.origin_stop_ref,
                    "destination_ref": snap.destination_stop_ref,
                    "recorded_at": snap.recorded_utc.isoformat(),
                    "lat": snap.lat,
                    "lon": snap.lon,
                    "bearing": snap.bearing,
                    "block_ref": snap.block_ref,
                    "chosen_trip_id": match.trip_id,
                    "match_tier": match.tier,
                    **clues,
                    **explanation,
                })
            except Exception:
                # Debug evidence must never interrupt the live collector.
                counters["evidence_dropped"] += 1
                counters["evidence_errors"] += 1
                logger.exception(
                    "could not prepare matching evidence for vehicle %s",
                    snap.vehicle_ref)

    live_db.record_poll(live_conn, "siri_vm", ok=True)
    live_conn.commit()
    written = _write_audit(audit_conn, backlog, audit_ops, evidence,
                           poll_at.isoformat(), True, counters, target_tz)
    return {"ok": True, "audit_written": written, **counters}


def sx_cycle(fetch, live_conn, boundary: BoundaryFilter, observed_nocs: set) -> dict:
    """One SIRI-SX poll: upsert in-scope situations, close the vanished."""
    xml = fetch()
    if xml is None:
        live_db.record_poll(live_conn, "siri_sx", ok=False)
        live_conn.commit()
        return {"ok": False}
    sits = parse_situations(xmltodict.parse(xml))
    kept = [s for s in sits if in_scope(s, boundary.contains, observed_nocs)]
    now = datetime.now(timezone.utc).isoformat()
    seen = set()
    for s in kept:
        # Source namespaces are independent. Legacy unscoped rows are withdrawn
        # by the existing seen-set comparison on the first successful new poll.
        key = json.dumps([s.participant, s.situation_number], separators=(",", ":"))
        seen.add(key)
        live_conn.execute(
            """INSERT INTO situations (situation_number, version, participant,
                   progress, planned, reason, summary, description, advice,
                   severity, validity_start, validity_end, versioned_at, link,
                   affected_json, closed_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)
               ON CONFLICT(situation_number) DO UPDATE SET
                   version=excluded.version, progress=excluded.progress,
                   summary=excluded.summary, description=excluded.description,
                   advice=excluded.advice, severity=excluded.severity,
                   validity_start=excluded.validity_start,
                   validity_end=excluded.validity_end,
                   versioned_at=excluded.versioned_at, link=excluded.link,
                   affected_json=excluded.affected_json, closed_at=NULL,
                   updated_at=excluded.updated_at
               WHERE excluded.version >= situations.version""",
            (key, s.version, s.participant, s.progress,
             int(s.planned), s.reason, s.summary, s.description, s.advice,
             s.severity, s.validity_start, s.validity_end, s.versioned_at,
             s.link, s.affected_json, now))
    if seen:
        q = ",".join("?" * len(seen))
        live_conn.execute(
            f"UPDATE situations SET closed_at=? WHERE closed_at IS NULL "
            f"AND situation_number NOT IN ({q})", (now, *seen))
    else:
        live_conn.execute(
            "UPDATE situations SET closed_at=? WHERE closed_at IS NULL", (now,))
    live_db.record_poll(live_conn, "siri_sx", ok=True)
    live_conn.commit()
    return {"ok": True, "in_feed": len(sits), "in_scope": len(kept)}


_running = True


def _stop(signum, frame):  # pragma: no cover
    global _running
    _running = False
    logger.info("signal %s: stopping after current cycle", signum)


def main() -> None:  # pragma: no cover - exercised by the smoke run
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    install_query_secret_filter()
    cfg = Config()
    cfg.require_key()
    target_tz = ZoneInfo(cfg.target_tz)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    boundary = BoundaryFilter(cfg.boundary_geojson)
    tt = sqlite3.connect(f"file:{cfg.timetable_db}?mode=ro", uri=True)
    live_conn = live_db.connect(cfg.live_db)
    audit_conn = audit_db.connect(cfg.audit_db)
    session = make_session()

    logger.info("collector starting: poll=%ss sx=%ss exact_match=%s",
                cfg.poll_interval_s, cfg.sx_poll_interval_s, cfg.enable_exact_match)
    last_sx = 0.0
    last_prune = 0.0
    backlog = AuditBacklog()
    while _running:
        t0 = time.time()
        if t0 - last_prune >= 3600:  # hourly; first cycle too
            pruned = live_db.prune_consumed_events(live_conn)
            if pruned:
                logger.info("events: pruned %s consumed rows older than %s days",
                            pruned, live_db.EVENT_RETENTION_DAYS)
            last_prune = t0
        r = vm_cycle(lambda: fetch_vm(session, cfg), tt.cursor(), live_conn,
                     audit_conn, boundary, cfg, target_tz, backlog=backlog)
        logger.info("vm: ok=%s total=%s in-area=%s matched=%s obs=%s events=%s"
                    " audit=%s",
                    r.get("ok"), r.get("vehicles_total"), r.get("candidates"),
                    r.get("matched"), r.get("obs_written"), r.get("events"),
                    "written" if r.get("audit_written") else "held")
        # Each ping follows a feed attempt that returned and committed, so a
        # hung request, parser or SQLite operation still cannot falsely keep
        # the service alive. Pinging after each fetch pass gives SIRI-VM and
        # SIRI-SX their own 120 s allowance instead of sharing one.
        notify_watchdog()
        if time.time() - last_sx >= cfg.sx_poll_interval_s:
            nocs = {row[0] for row in live_conn.execute(
                "SELECT DISTINCT operator_ref FROM vehicles")}
            rs = sx_cycle(lambda: fetch_sx(session, cfg), live_conn, boundary, nocs)
            logger.info("sx: %s", rs)
            last_sx = time.time()
            notify_watchdog()
        elapsed = time.time() - t0
        for _ in range(max(1, int(cfg.poll_interval_s - elapsed))):
            if not _running:
                break
            time.sleep(1)
    logger.info("collector stopped")


if __name__ == "__main__":
    main()

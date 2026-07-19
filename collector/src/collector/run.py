"""The collector's main loop: fetch -> filter -> match -> measure -> write.

Everything hard is in the tested modules; this file is assembly. The cycle
functions take an injected `fetch` callable so tests drive them with canned
XML and no network.

Run:  python -m collector.run   (from collector/, with .env + timetable.db)
"""
from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import xmltodict

from . import audit_db, live_db
from .config import Config
from .delay import live_estimate, settled_reading
from .geo import BoundaryFilter
from .matching import match_vehicle
from .siri import (activities_from_xmltodict, anchor_departure_local,
                   clean_destination, extract_snapshot)
from .sirisx import in_scope, parse_situations
from .secret_filter import install_query_secret_filter, redact_query_secrets
from .timeparse import gtfs_seconds, service_midnight

logger = logging.getLogger("collector")

SIRI_VM_URL = "https://data.bus-data.dft.gov.uk/api/v1/datafeed/"
SIRI_SX_URL = "https://data.bus-data.dft.gov.uk/api/v1/siri-sx/"


def make_session() -> requests.Session:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1.0,
                  status_forcelist=[429, 500, 502, 503, 504],
                  respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def fetch_vm(session: requests.Session, cfg: Config) -> str | None:
    try:
        r = session.get(
            f"{SIRI_VM_URL}?boundingBox={quote(cfg.bounding_box)}&api_key={cfg.bods_api_key}",
            timeout=cfg.fetch_timeout_s)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        logger.warning("SIRI-VM fetch failed: %s", redact_query_secrets(e))
        return None


def fetch_sx(session: requests.Session, cfg: Config) -> str | None:
    try:
        r = session.get(f"{SIRI_SX_URL}?api_key={cfg.bods_api_key}",
                        timeout=cfg.fetch_timeout_s)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        logger.warning("SIRI-SX fetch failed: %s", redact_query_secrets(e))
        return None


def vm_cycle(fetch, tt_cur, live_conn, audit_conn, boundary: BoundaryFilter,
             cfg: Config, target_tz, now_utc: datetime | None = None) -> dict:
    """One SIRI-VM poll. Returns counters (also written to both DBs).
    `now_utc` is injectable so tests are independent of the wall clock."""
    poll_at = now_utc or datetime.now(timezone.utc)
    xml = fetch()
    counters = {"vehicles_total": 0, "candidates": 0, "matched": 0,
                "obs_written": 0, "dropped_insane": 0, "events": 0,
                "stale": 0}

    if xml is None:
        live_db.record_poll(live_conn, "siri_vm", ok=False)
        audit_db.log_poll(audit_conn, poll_at.isoformat(), False, counters)
        live_conn.commit(); audit_conn.commit()
        return {"ok": False, **counters}

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
        if origin_local is not None and \
                (now_utc - origin_local.astimezone(timezone.utc)) <= timedelta(hours=cfg.max_journey_age_h):
            match = match_vehicle(tt_cur, snap.operator_ref, snap.line,
                                  snap.direction, origin_local,
                                  snap.journey_ref, cfg.enable_exact_match,
                                  vehicle_pos=(snap.lat, snap.lon))
        if match:
            counters["matched"] += 1
            first_secs = gtfs_seconds(match.schedule[0][1])
            if first_secs is not None:
                sm = service_midnight(origin_local, first_secs)
                est = live_estimate(snap.lat, snap.lon, snap.recorded_utc,
                                    match.schedule, sm)
                reading = settled_reading(snap.lat, snap.lon, snap.recorded_utc,
                                          match.schedule, sm)
                if reading is not None:
                    audit_db.upsert_observation(audit_conn.cursor(), (
                        sm.strftime("%Y%m%d"), snap.operator_ref,
                        match.route_short_name, match.trip_id, snap.journey_ref,
                        reading.stop_sequence, reading.stop_code,
                        reading.scheduled_local.isoformat(),
                        reading.observed_delay_s, int(reading.on_time),
                        reading.gps_distance_m, snap.recorded_utc.isoformat(),
                        snap.vehicle_ref))
                    counters["obs_written"] += 1

        decision = live_db.upsert_vehicle(live_conn, snap, est, match,
                                          destination=clean_destination(snap.destination_raw))
        if decision.emit:
            counters["events"] += 1

    live_db.record_poll(live_conn, "siri_vm", ok=True)
    audit_db.log_poll(audit_conn, poll_at.isoformat(), True, counters)
    live_conn.commit(); audit_conn.commit()
    return {"ok": True, **counters}


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
        seen.add(s.situation_number)
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
            (s.situation_number, s.version, s.participant, s.progress,
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
    import sqlite3
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s — %(message)s")
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
    while _running:
        t0 = time.time()
        if t0 - last_prune >= 3600:  # hourly; first cycle too
            pruned = live_db.prune_consumed_events(live_conn)
            if pruned:
                logger.info("events: pruned %s consumed rows older than %s days",
                            pruned, live_db.EVENT_RETENTION_DAYS)
            last_prune = t0
        r = vm_cycle(lambda: fetch_vm(session, cfg), tt.cursor(), live_conn,
                     audit_conn, boundary, cfg, target_tz)
        logger.info("vm: ok=%s total=%s in-area=%s matched=%s obs=%s events=%s",
                    r.get("ok"), r.get("vehicles_total"), r.get("candidates"),
                    r.get("matched"), r.get("obs_written"), r.get("events"))
        if time.time() - last_sx >= cfg.sx_poll_interval_s:
            nocs = {row[0] for row in live_conn.execute(
                "SELECT DISTINCT operator_ref FROM vehicles")}
            rs = sx_cycle(lambda: fetch_sx(session, cfg), live_conn, boundary, nocs)
            logger.info("sx: %s", rs)
            last_sx = time.time()
        elapsed = time.time() - t0
        for _ in range(max(1, int(cfg.poll_interval_s - elapsed))):
            if not _running:
                break
            time.sleep(1)
    logger.info("collector stopped")


if __name__ == "__main__":
    main()

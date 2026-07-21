#!/usr/bin/env python3
"""
Merge First routes that BODS dropped from the GTFS export into the timetable, by
parsing First's own TransXChange (the authoritative registration data) with the
bustimes.org parser.

The BODS regional GTFS is a lossy conversion that silently drops complex routes
(e.g. First's 42-45). Those routes ARE in First's TransXChange. This:
  1. Reads which First routes are missing from the freshly built timetable.
  2. Parses First's downloaded TXC datasets and, for each missing route, emits
     routes + trips + stop_times (with timepoint) + calendar straight into the
     timetable, reusing existing stop coordinates (falling back to a known-good
     stops table for any stop the GTFS build pruned).

Supplemented ids are prefixed 'SUP_' and attached to the timetable's FBRI agency.

Usage:
    python audit_txc_to_timetable.py <timetable.db> <first_txc_dir> [known_good.db]
"""
import os
import re
import sys
import glob
import zipfile
import sqlite3

import txc_parser as txc

LINE_RE = re.compile(br"<LineName>([^<]+)</LineName>")

# TimingStatus may use either the long form or the TXC three-letter code.
PRINCIPAL = "principal"
PTP_CODE = "ptp"


def td_to_gtfs(delta):
    if delta is None:
        return None
    total = int(delta.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def weekday_flags(operating_profile):
    flags = [0] * 7
    if operating_profile is None:
        return None
    for d in operating_profile.regular_days:
        if 0 <= d.day <= 6:
            flags[d.day] = 1
    return flags if any(flags) else None


def ymd(date, default):
    return date.strftime("%Y%m%d") if date else default


def load_stop_index(conn):
    idx = {}
    for sid, code in conn.execute("SELECT stop_id, stop_code FROM stops"):
        if sid:
            idx.setdefault(str(sid).upper(), sid)
        if code:
            idx.setdefault(str(code).upper(), sid)
    return idx


def main():
    if len(sys.argv) < 3:
        print("Usage: audit_txc_to_timetable.py <timetable.db> <first_txc_dir> [known_good.db]")
        return 1
    db_path, txc_dir = sys.argv[1], sys.argv[2]
    good_path = sys.argv[3] if len(sys.argv) > 3 else None

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    agency_row = cur.execute("SELECT agency_id FROM agency WHERE agency_noc='FBRI' LIMIT 1").fetchone()
    if not agency_row:
        print("ERROR: no FBRI agency in timetable.")
        return 1
    fbri_agency = agency_row[0]

    target_first = {r[0] for r in cur.execute(
        "SELECT DISTINCT r.route_short_name FROM routes r JOIN agency a "
        "ON r.agency_id=a.agency_id WHERE a.agency_noc='FBRI'")}
    stop_index = load_stop_index(conn)

    good = sqlite3.connect(good_path) if good_path and os.path.exists(good_path) else None
    good_stops = {}
    if good:
        for sid, code, lat, lon, name in good.execute(
                "SELECT stop_id, stop_code, stop_lat, stop_lon, stop_name FROM stops"):
            for key in (sid, code):
                if key:
                    good_stops.setdefault(str(key).upper(), (code or sid, lat, lon, name))

    zips = sorted(glob.glob(os.path.join(txc_dir, "*.zip")))
    if not zips:
        print(f"No TXC zips in {txc_dir}")
        return 1
    print(f"Scanning {len(zips)} TXC zips for First routes missing from the GTFS build...", flush=True)

    calendar_ids = {}
    routes_added = set()
    n_trips = n_st = n_stops_added = 0
    seq_counter = 0
    errors = []

    def ensure_stop(atco):
        nonlocal n_stops_added
        key = atco.upper()
        if key in stop_index:
            return stop_index[key]
        if key in good_stops:
            code, lat, lon, name = good_stops[key]
            cur.execute(
                "INSERT OR IGNORE INTO stops (stop_id, stop_code, stop_name, stop_lat, stop_lon) "
                "VALUES (?,?,?,?,?)", (atco, code, name, lat, lon))
            stop_index[key] = atco
            n_stops_added += 1
            return atco
        return None

    def ensure_calendar(profile, period):
        flags = weekday_flags(profile)
        if not flags:
            return None
        start = ymd(period.start if period else None, "20200101")
        end = ymd(period.end if period else None, "20270101")
        key = (tuple(flags), start, end)
        if key in calendar_ids:
            return calendar_ids[key]
        sid = f"SUP_S_{len(calendar_ids)}"
        cur.execute(
            "INSERT OR IGNORE INTO calendar (service_id, monday, tuesday, wednesday, "
            "thursday, friday, saturday, sunday, start_date, end_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, *flags, start, end))
        calendar_ids[key] = sid
        return sid

    for zpath in zips:
        try:
            zf = zipfile.ZipFile(zpath)
        except zipfile.BadZipFile as exc:
            errors.append(f"bad zip {os.path.basename(zpath)}: {exc}")
            continue
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            raw = zf.read(name)
            # only First BRISTOL files (these datasets bundle every First region)
            if b"FBRI" not in raw:
                continue
            file_lines = {
                value.decode("utf-8", "ignore").split("|", 1)[0].strip()
                for value in LINE_RE.findall(raw)
            }
            new_lines = sorted(file_lines - target_first)
            if not new_lines:
                continue
            print(f"  parsing {os.path.basename(zpath)}/{name}  (missing lines: {new_lines})", flush=True)
            try:
                del raw
                with zf.open(name) as xml_file:
                    doc = txc.TransXChange(xml_file)
            except Exception as e:
                print(f"    parse failed: {e}", flush=True)
                errors.append(
                    f"parse failed {os.path.basename(zpath)}/{name}: {e}")
                continue
            noc_map = {}
            if getattr(doc, "operators", None) is not None:
                for op in doc.operators:
                    oid = op.get("id")
                    if oid:
                        noc_map[oid] = op.findtext("NationalOperatorCode")
            file_nocs = {v for v in noc_map.values() if v}
            for service in doc.services.values():
                svc_noc = noc_map.get(service.operator)
                if svc_noc != "FBRI" and not (svc_noc is None and file_nocs == {"FBRI"}):
                    continue  # skip non-Bristol First services
                for line in service.lines:
                    ln = line.line_name
                    if not ln or ln in target_first:
                        continue  # already have it from GTFS, or no name
                    journeys = doc.get_journeys(service.service_code, line.id)
                    if not journeys:
                        continue
                    route_id = f"SUP_R_{ln}"
                    if route_id not in routes_added:
                        cur.execute(
                            "INSERT OR IGNORE INTO routes (route_id, agency_id, route_short_name, route_type) "
                            "VALUES (?,?,?,3)", (route_id, fbri_agency, ln))
                        routes_added.add(route_id)
                    for j in journeys:
                        profile = j.operating_profile or service.operating_profile
                        service_id = ensure_calendar(profile, service.operating_period)
                        if not service_id:
                            continue
                        jp = j.journey_pattern
                        direction = 1 if (jp and jp.is_inbound()) else 0
                        trip_id = f"SUP_T_{service.service_code}_{j.code}"
                        if cur.execute(
                                "SELECT 1 FROM trips WHERE trip_id=? LIMIT 1",
                                (trip_id,)).fetchone():
                            continue
                        try:
                            cells = list(j.get_times())
                        except Exception as exc:
                            errors.append(
                                f"journey parse failed {trip_id}: {exc}")
                            continue
                        seq = 0
                        wrote_any = False
                        for cell in cells:
                            atco = cell.stopusage.stop.atco_code
                            stop_id = ensure_stop(atco)
                            if not stop_id:
                                continue
                            seq += 1
                            dep = td_to_gtfs(cell.departure_time) or td_to_gtfs(cell.arrival_time)
                            arr = td_to_gtfs(cell.arrival_time) or dep
                            ts = (cell.stopusage.timingstatus or "").lower().strip()
                            timepoint = 1 if (PRINCIPAL in ts or ts == PTP_CODE) else 0
                            cur.execute(
                                "INSERT OR IGNORE INTO stop_times (trip_id, arrival_time, departure_time, "
                                "stop_id, stop_sequence, timepoint) VALUES (?,?,?,?,?,?)",
                                (trip_id, arr, dep, stop_id, seq, timepoint))
                            n_st += 1
                            wrote_any = True
                        if wrote_any:
                            cur.execute(
                                "INSERT OR IGNORE INTO trips (trip_id, route_id, service_id, direction_id) "
                                "VALUES (?,?,?,?)", (trip_id, route_id, service_id, direction))
                            n_trips += 1
        zf.close()

    if errors:
        conn.rollback()
        print("ERROR: TXC merge was incomplete:", flush=True)
        for error in errors[:20]:
            print(f"  {error}", flush=True)
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more", flush=True)
        conn.close()
        if good:
            good.close()
        return 1
    conn.commit()
    added_routes = sorted({r.replace("SUP_R_", "") for r in routes_added})
    print(f"TXC merge: added First routes {added_routes}")
    print(f"  trips={n_trips} stop_times={n_st} stops_added={n_stops_added} calendars={len(calendar_ids)}")
    conn.close()
    if good:
        good.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

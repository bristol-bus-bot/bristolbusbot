#!/usr/bin/env python3
"""Build and validate the timetable used by the live services and audit."""
import os
import sys
import csv
import json
import shutil
import sqlite3
import tempfile
import logging
import subprocess
from pathlib import Path
from datetime import date

from timetable_editions import normalize_database as normalize_route_editions

HERE = Path(__file__).parent
PY = sys.executable
# Use the system temporary directory for downloaded and intermediate data.
TMP = Path(tempfile.gettempdir())
GTFS_DIR = TMP / "busaudit_gtfs"
WECA_DB = TMP / "busaudit_timetable_weca.db"
SOURCE_STATUS = TMP / "busaudit_timetable_source_status.json"
# BBB_TIMETABLE_DB selects the finished local database path.
TIMETABLE_DB = Path(os.getenv("BBB_TIMETABLE_DB", str(HERE / "timetable.db")))
# An existing timetable may supply stop coordinates missing from source data.
BUSBOT_DB = Path(os.getenv("BUSBOT_FALLBACK_DB",
                           str(HERE.parent / "bot" / "data" / "timetable.db")))

# Required First routes used to reject incomplete source exports.
EXPECTED_FBRI = ["1", "2", "42", "43", "44", "45", "75", "76", "X1", "m1"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("build_timetable.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def run(cmd):
    logger.info("RUN: " + " ".join(str(c) for c in cmd))
    # Use UTF-8 consistently across Windows and Linux child processes.
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(cmd, env=env).returncode == 0


def write_source_status(*, tnds_status: str,
                        missing_before_tnds: list[str]) -> None:
    """Record whether the legacy TNDS fallback contributed to this build."""
    payload = {
        "schema": 1,
        "tnds": {
            "status": tnds_status,
            "missing_before_fallback": missing_before_tnds,
        },
    }
    temporary = SOURCE_STATUS.with_name(
        f".{SOURCE_STATUS.name}.new-{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    os.replace(temporary, SOURCE_STATUS)


def validate(db_path, *, today: date | None = None):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    fbri = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT r.route_short_name FROM routes r "
            "JOIN agency a ON r.agency_id = a.agency_id WHERE a.agency_noc = 'FBRI'"
        )
    }
    latest_calendar = conn.execute(
        "SELECT MAX(end_date) FROM calendar").fetchone()[0]
    latest_exception = conn.execute(
        "SELECT MAX(date) FROM calendar_dates WHERE exception_type=1").fetchone()[0]
    conn.close()
    missing = [r for r in EXPECTED_FBRI if r not in fbri]
    latest_service = max(
        (value for value in (latest_calendar, latest_exception) if value),
        default=None)
    today_text = (today or date.today()).strftime("%Y%m%d")
    return {
        "fbri_count": len(fbri),
        "missing": missing,
        "integrity": integrity,
        "latest_service": latest_service,
        "stale": latest_service is None or latest_service < today_text,
    }


def promote_atomically(staged_db: Path, live_db: Path) -> Path:
    """Keep one rollback copy and atomically replace the live pathname."""
    previous_db = live_db.with_name(f"{live_db.name}.previous")
    if live_db.exists():
        shutil.copy2(live_db, previous_db)
    os.replace(staged_db, live_db)
    return previous_db


def finalize_static_database(path: Path) -> None:
    """Optimize the candidate and make the published DB truly read-only.

    Runtime consumers never write this database, so DELETE journal mode avoids
    requiring writable `-wal`/`-shm` sidecars inside their sandboxes.
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        conn.execute("ANALYZE")
        conn.execute("PRAGMA optimize").fetchall()
        conn.commit()
        conn.execute("VACUUM")
        mode = conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if mode.lower() != "delete":
            raise RuntimeError(f"could not set static journal mode: {mode}")
    finally:
        conn.close()
    Path(f"{path}-wal").unlink(missing_ok=True)
    Path(f"{path}-shm").unlink(missing_ok=True)


def diagnose_missing(gtfs_dir, missing):
    """Report which operators (if any) run the missing route numbers in the
    freshly downloaded GTFS, so we know whether First's routes are hiding under
    a different operator code or are genuinely absent from BODS South West."""
    routes_f = Path(gtfs_dir) / "routes.txt"
    agency_f = Path(gtfs_dir) / "agency.txt"
    if not routes_f.exists() or not agency_f.exists():
        logger.info("  (fresh GTFS not available to diagnose)")
        return
    agencies = {}
    with open(agency_f, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            agencies[row["agency_id"]] = row.get("agency_noc") or "?"
    found = {m: set() for m in missing}
    with open(routes_f, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sn = row.get("route_short_name")
            if sn in found:
                found[sn].add(agencies.get(row.get("agency_id"), "?"))
    logger.info("  Where the missing numbers appear in this BODS GTFS:")
    for m in missing:
        ops = sorted(found[m])
        logger.info(f"    {m}: {', '.join(ops) if ops else 'NOT PRESENT under any operator'}")


def main():
    args = sys.argv[1:]
    no_download = "--no-download" in args
    skip_deploy = "--skip-deploy" in args

    if not skip_deploy:
        logger.error(
            "Use python "
            "deploy/push.py --refresh-timetable so validation, remote "
            "promotion, consumer health checks and rollback cannot be bypassed.")
        return 2

    SOURCE_STATUS.unlink(missing_ok=True)

    logger.info("=" * 80)
    logger.info("AUDIT TIMETABLE UPDATE")
    logger.info("=" * 80)

    build_cmd = [PY, str(HERE / "build_timetable_weca.py"),
                 "--gtfs", str(GTFS_DIR), "--output", str(WECA_DB)]
    if no_download:
        build_cmd.append("--no-download")
    if not run(build_cmd):
        logger.error("Timetable build failed - aborting.")
        return 1
    if not WECA_DB.exists():
        logger.error(f"{WECA_DB.name} was not produced - aborting.")
        return 1

    # Supplement First routes BODS dropped from the lossy GTFS, by parsing
    # First's own TransXChange (the authoritative registration data).
    txc_dir = TMP / "busaudit_first_txc"
    if no_download:
        if not txc_dir.is_dir() or not any(txc_dir.glob("*.zip")):
            logger.error(
                "Cached First TransXChange source is missing from %s.", txc_dir)
            return 1
    elif not run([PY, str(HERE / "audit_fetch_first_txc.py")]):
        logger.error("Fetching First's TransXChange failed - aborting.")
        return 1
    merge_cmd = [PY, str(HERE / "audit_txc_to_timetable.py"), str(WECA_DB), str(txc_dir)]
    if BUSBOT_DB.exists():
        merge_cmd.append(str(BUSBOT_DB))  # stop-coordinate fallback
    if not run(merge_cmd):
        logger.error("TXC merge failed - aborting.")
        return 1

    # TNDS is an older, slower FTP source. It is a safety fallback, not a
    # compulsory dependency when BODS GTFS plus First's own TXC already meet
    # the completeness contract.
    primary_validation = validate(WECA_DB)
    missing_before_tnds = list(primary_validation["missing"])
    tnds_dir = TMP / "busaudit_tnds"
    if missing_before_tnds:
        logger.warning(
            "Primary sources are missing required First routes %s; "
            "using the TNDS fallback.", missing_before_tnds)
        if no_download:
            tnds_ready = tnds_dir.is_dir() and any(tnds_dir.glob("*.zip"))
            if not tnds_ready:
                logger.error(
                    "TNDS fallback is required but its cache is missing from %s.",
                    tnds_dir)
                return 1
        else:
            tnds_ready = run([PY, str(HERE / "audit_fetch_tnds.py")])
        if not tnds_ready:
            logger.error(
                "TNDS fallback was required but could not be fetched - "
                "aborting; partial timetable candidates are never published.")
            return 1
        tnds_merge = [PY, str(HERE / "audit_txc_to_timetable.py"),
                      str(WECA_DB), str(tnds_dir)]
        if BUSBOT_DB.exists():
            tnds_merge.append(str(BUSBOT_DB))
        if not run(tnds_merge):
            logger.error("TNDS fallback merge failed - aborting.")
            return 1
        write_source_status(
            tnds_status="fallback_used",
            missing_before_tnds=missing_before_tnds)
    else:
        logger.info(
            "Primary BODS and First TXC sources already contain every "
            "required First route; TNDS fallback is not needed.")
        write_source_status(
            tnds_status="not_needed", missing_before_tnds=[])

    # BODS can publish current and future revisions of the same registered
    # route with overlapping calendar ranges. Preserve every revision, but
    # make replacement-like editions effective one at a time.
    logger.info("Resolving overlapping timetable editions...")
    try:
        edition_result = normalize_route_editions(WECA_DB)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        logger.error(
            "Route-edition normalization failed - refusing the candidate: %s",
            exc)
        return 2
    logger.info(
        "Resolved %s superseded route editions; re-windowed %s trips.",
        edition_result["superseded_route_editions"],
        edition_result["trips_rewindowed"],
    )

    validation = validate(WECA_DB)
    logger.info(
        "Rebuilt timetable lists %s First routes; latest service date %s.",
        validation["fbri_count"], validation["latest_service"] or "missing")
    if validation["integrity"] != "ok":
        logger.error("VALIDATION FAILED: SQLite integrity_check returned %r.",
                     validation["integrity"])
        return 2
    if validation["stale"]:
        logger.error(
            "VALIDATION FAILED: timetable service ends at %s, before today. "
            "Refusing to publish stale schedules.", validation["latest_service"])
        return 2
    if validation["missing"]:
        logger.error("=" * 80)
        logger.error(
            "VALIDATION FAILED: First is missing routes %s.",
            validation["missing"])
        logger.error("This timetable would silently under-report those routes.")
        logger.error("NOT promoting and NOT deploying. The old timetable stays in place.")
        logger.error("=" * 80)
        diagnose_missing(GTFS_DIR, validation["missing"])
        return 2
    logger.info("Validation passed: integrity, freshness and expected routes are good.")

    # Complete every remaining mutation on a sibling staging file. Readers
    # continue using the old timetable until one atomic os.replace at the end.
    TIMETABLE_DB.parent.mkdir(parents=True, exist_ok=True)
    staged_db = TIMETABLE_DB.with_name(
        f".{TIMETABLE_DB.name}.new-{os.getpid()}")
    if staged_db.exists():
        staged_db.unlink()
    shutil.copy2(WECA_DB, staged_db)

    # Route shapes MUST come from the same GTFS extract the timetable was
    # built from — shape_ids regenerate per BODS build, so importing from
    # any other download matches nothing (shape_ids are per-extract UUIDs).
    if (Path(GTFS_DIR) / "shapes.txt").exists():
        env_shapes = {**os.environ, "BBB_TIMETABLE_DB": str(staged_db),
                      "BBB_GTFS_DIR": str(GTFS_DIR),
                      "BBB_CANDIDATE_BUILD": "1",
                      "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        logger.info("Importing route shapes from the same GTFS extract...")
        if subprocess.run([PY, str(HERE / "import_shapes.py")],
                          env=env_shapes).returncode != 0:
            staged_db.unlink(missing_ok=True)
            logger.error("Shapes import failed - refusing to publish a partial timetable.")
            return 2
    else:
        staged_db.unlink(missing_ok=True)
        logger.error("No shapes.txt in the GTFS extract - refusing to publish a partial timetable.")
        return 2

    # Stop search needs the distinct route names serving each stop. Computing
    # that relationship from millions of stop_times inside a web request can
    # exceed the Pi's worker timeout, so materialise it once in the disposable
    # candidate after every source merge has completed.
    logger.info("Precomputing compact stop-search route lookup...")
    if not run([PY, str(HERE / "prepare_stop_routes.py"), str(staged_db)]):
        staged_db.unlink(missing_ok=True)
        logger.error(
            "Stop-search lookup generation failed - refusing to publish the candidate.")
        return 2

    try:
        finalize_static_database(staged_db)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        staged_db.unlink(missing_ok=True)
        logger.error("Could not finalize static timetable database: %s", exc)
        return 2

    final_validation = validate(staged_db)
    if final_validation["integrity"] != "ok" or final_validation["stale"] \
            or final_validation["missing"]:
        staged_db.unlink(missing_ok=True)
        logger.error("Final staged timetable failed validation: %s", final_validation)
        return 2

    try:
        promote_atomically(staged_db, TIMETABLE_DB)
    except OSError as exc:
        staged_db.unlink(missing_ok=True)
        logger.error("Atomic timetable promotion failed; old database is untouched: %s", exc)
        return 2
    logger.info("Atomically promoted validated timetable -> %s", TIMETABLE_DB)

    logger.info("Internal --skip-deploy hand-off complete: built and validated; push.py owns deployment.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

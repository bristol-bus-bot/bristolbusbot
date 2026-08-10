#!/usr/bin/env python3
"""Audit and refresh fleet, description and route-shape enrichment.

    python refresh_enrichment.py             audit only (safe, no writes)
    python refresh_enrichment.py --fix       build a fleet shadow candidate
                                             and import available shapes

Description generation now runs only through the Pi's fail-closed pending-review
workflow. For shapes, --fix requires BBB_GTFS_DIR containing shapes.txt plus
BBB_TIMETABLE_DB. Steps that lack requirements are reported and skipped.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SITE = REPO / "site"

FLEET = HERE / "fbribuses.json"
FLEET_CANDIDATE = HERE / "fbribuses.candidate.json"
FLEET_REPORT = HERE / "fleet-shadow-report.json"
BLURB_SETS = {
    "in-service": HERE / "bus-descriptions.json",
    "depot": HERE / "depot-descriptions.json",
    "waiting": HERE / "waiting-descriptions.json",
}
_WHITES = {"#fff", "#FFF", "#ffffff", "#FFFFFF", "white"}


def load_json(path: Path):
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def audit() -> dict:
    # Prefer the staged fleet cache, falling back to the site's local copy.
    src = FLEET if FLEET.exists() else SITE / "fbribuses.json"
    fleet = load_json(src)
    report: dict = {"generated": datetime.now().isoformat(timespec="seconds"),
                    "fleet_source": str(src.relative_to(REPO))}
    if not isinstance(fleet, list):
        report["fleet"] = "MISSING everywhere — run with --fix"
        return report
    active = [v for v in fleet if not v.get("withdrawn")]
    no_livery = [v for v in active
                 if not (v.get("livery") or {}).get("left")
                 or (v.get("livery") or {}).get("left") in _WHITES]
    report["fleet"] = {
        "vehicles": len(fleet), "active": len(active),
        "no_or_white_livery": len(no_livery),
        "no_livery_examples": sorted(
            str(v.get("fleet_code") or v.get("reg") or "?")
            for v in no_livery)[:15],
    }
    codes = {str(v.get("fleet_code") or v.get("fleet_number") or "")
             for v in active} - {""}
    for name, path in BLURB_SETS.items():
        blurbs = load_json(path if path.exists()
                           else SITE / path.name) or {}
        missing = sorted(codes - set(blurbs))
        report[f"blurbs_{name}"] = {
            "have": len(blurbs), "missing_for_active_fleet": len(missing),
            "missing_examples": missing[:15],
        }
    # Check whether route shapes are present in the selected timetable.
    db = os.getenv("BBB_TIMETABLE_DB", "")
    if db and Path(db).exists():
        import sqlite3
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = conn.execute("SELECT name FROM sqlite_master WHERE "
                           "name='route_shapes'").fetchone()
        n = conn.execute("SELECT COUNT(*) FROM route_shapes").fetchone()[0] \
            if row else 0
        conn.close()
        report["route_shapes"] = {"table": bool(row), "rows": n}
    else:
        report["route_shapes"] = "set BBB_TIMETABLE_DB to audit"
    return report


def scoped_blurb_payload(fleet: list[dict],
                         observed: set[tuple[str, str]]) -> dict:
    """Build a fail-closed scope without collapsing operators into one code."""
    from audit_vehicle_identity import (
        fleet_code,
        indexes,
        normalise_registration,
        safe_match,
    )
    index = indexes(fleet)
    shared = {
        code for code, owners in index["active_code_operators"].items()
        if len(owners) > 1
    }
    scoped_keys: set[str] = set()
    legacy_codes: set[str] = set()
    registrations: set[str] = set()
    unresolved = 0
    for operator, vehicle_ref in observed:
        record = safe_match(index, operator, vehicle_ref)
        if record is None:
            unresolved += 1
            continue
        code = fleet_code(record)
        registration = normalise_registration(record.get("reg"))
        if code:
            scoped_keys.add(f"{operator}:{code}")
            # Existing generators may consume only the legacy list. Never put
            # a cross-operator collision into that unsafe compatibility seam.
            if code not in shared:
                legacy_codes.add(code)
        if registration:
            registrations.add(registration)
    return {
        "schema": 2,
        "observed_identities": len(observed),
        "matched_identities": len(observed) - unresolved,
        "unresolved_identities": unresolved,
        "scoped_keys": sorted(scoped_keys),
        "registrations": sorted(registrations),
        # Backward-compatible key for the current generators. Ambiguous codes
        # are deliberately absent until they emit operator-scoped keys.
        "codes": sorted(legacy_codes),
    }


def build_blurb_scope() -> None:
    """Write operator-scoped identities observed by the collector in WECA."""
    import sqlite3
    observed: set[tuple[str, str]] = set()
    for db, sql in ((REPO / "collector" / "live.db",
                     "SELECT DISTINCT operator_ref, vehicle_ref FROM vehicles"),
                    (REPO / "collector" / "audit.db",
                     "SELECT DISTINCT operator, vehicle_ref "
                     "FROM timepoint_observations")):
        if not db.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            observed.update(
                (str(operator or "").strip().upper(),
                 str(vehicle_ref or "").strip())
                for operator, vehicle_ref in conn.execute(sql)
                if str(operator or "").strip()
                and str(vehicle_ref or "").strip())
            conn.close()
        except sqlite3.Error as e:
            print(f"(scope: could not read {db.name}: {e})")
    source = FLEET if FLEET.exists() else SITE / "fbribuses.json"
    fleet = load_json(source)
    if not isinstance(fleet, list):
        fleet = []
    payload = scoped_blurb_payload(fleet, observed) if fleet else {
        "schema": 2,
        "observed_identities": len(observed),
        "matched_identities": 0,
        "unresolved_identities": len(observed),
        "scoped_keys": [],
        "registrations": [],
        "codes": [],
    }
    payload["built"] = datetime.now().isoformat(timespec="seconds")
    out = HERE / "blurb_scope.json"
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("blurb scope: "
          f"{payload['observed_identities']} observed identities -> "
          f"{len(payload['scoped_keys'])} scoped keys, "
          f"{payload['unresolved_identities']} unresolved")


def run_step(label: str, argv: list[str]) -> bool:
    print(f"\n=== {label} ===")
    r = subprocess.run([sys.executable, *argv], cwd=HERE)
    ok = r.returncode == 0
    print(f"=== {label}: {'OK' if ok else f'FAILED (exit {r.returncode})'} ===")
    return ok


def distribute() -> None:
    """The old direct-to-release description copy is deliberately retired."""
    raise RuntimeError(
        "description distribution requires Pi pending review and approval")


def main() -> int:
    fix = "--fix" in sys.argv
    if fix:
        try:
            from dotenv import load_dotenv
            load_dotenv(HERE / ".env")
        except ImportError:
            pass
        live_fleet = FLEET if FLEET.exists() else SITE / "fbribuses.json"
        run_step("fleet shadow candidate (bustimes.org)", [
            "update_fleet_data.py",
            "--live", str(live_fleet),
            "--candidate", str(FLEET_CANDIDATE),
            "--report", str(FLEET_REPORT),
        ])
        print("\n(skipping direct blurb generation: use the Pi's pending-review "
              "workflow; this command cannot publish descriptions)")
        gtfs = Path(os.getenv("BBB_GTFS_DIR", HERE / "itm_south_west_gtfs"))
        if (gtfs / "shapes.txt").exists() and os.getenv("BBB_TIMETABLE_DB"):
            run_step("route shapes import", ["import_shapes.py"])
        else:
            print("\n(skipping shapes: need shapes.txt in BBB_GTFS_DIR and "
                  "BBB_TIMETABLE_DB set)")

    report = audit()
    print("\n" + "=" * 60)
    print("ENRICHMENT AUDIT" + ("" if fix else "  (read-only — use --fix to refresh)"))
    print("=" * 60)
    print(json.dumps(report, indent=2))
    (HERE / "enrichment_report.json").write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

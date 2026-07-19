#!/usr/bin/env python3
"""Audit and refresh fleet, description and route-shape enrichment.

    python refresh_enrichment.py             audit only (safe, no writes)
    python refresh_enrichment.py --fix       fetch fleet + generate missing
                                             blurbs + import shapes + copy
                                             fresh files to site/ and bot/

--fix requirements: network (bustimes.org), GEMINI_API_KEY in .env here
(for blurbs), and for shapes: BBB_GTFS_DIR containing shapes.txt plus
BBB_TIMETABLE_DB. Steps that lack their requirements are reported and skipped.
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
BOT_DATA = REPO / "bot" / "data"

FLEET = HERE / "fbribuses.json"
BLURB_SETS = {
    "in-service": HERE / "bus-descriptions.json",
    "depot": HERE / "depot-descriptions.json",
    "waiting": HERE / "waiting-descriptions.json",
}
GENERATORS = {
    "in-service": "generate_bus_descriptions.py",
    "depot": "generate_depot_descriptions.py",
    "waiting": "generate_waiting_descriptions.py",
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


def build_blurb_scope() -> None:
    """Write the vehicle codes observed by the collector in WECA."""
    import sqlite3
    refs: set[str] = set()
    for db, sql in ((REPO / "collector" / "live.db",
                     "SELECT DISTINCT vehicle_ref FROM vehicles"),
                    (REPO / "collector" / "audit.db",
                     "SELECT DISTINCT vehicle_ref FROM timepoint_observations")):
        if not db.exists():
            continue
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            refs.update(r[0] for r in conn.execute(sql) if r[0])
            conn.close()
        except sqlite3.Error as e:
            print(f"(scope: could not read {db.name}: {e})")
    if not refs:
        print("(scope: no collector data found - generators run UNFENCED; "
              "run the collector first to build the observed-vehicles list)")
        return
    codes = set()
    for ref in refs:
        codes.add(ref.split("-")[-1])                      # FBRI-12345 -> 12345
        codes.add(ref.upper().replace("_", "").replace("-", ""))  # reg forms
    out = HERE / "blurb_scope.json"
    out.write_text(json.dumps({"built": datetime.now().isoformat(timespec="seconds"),
                               "observed_refs": len(refs),
                               "codes": sorted(codes)}, indent=1))
    print(f"blurb scope: {len(refs)} observed vehicles -> {len(codes)} fleet codes")


def run_step(label: str, argv: list[str]) -> bool:
    print(f"\n=== {label} ===")
    r = subprocess.run([sys.executable, *argv], cwd=HERE)
    ok = r.returncode == 0
    print(f"=== {label}: {'OK' if ok else f'FAILED (exit {r.returncode})'} ===")
    return ok


def distribute() -> None:
    """Fresh enrichment files to their consumers (site/ and bot/data/)."""
    import shutil
    targets = {
        FLEET: [SITE / "fbribuses.json", BOT_DATA / "fbribuses.json"],
        BLURB_SETS["in-service"]: [SITE / "bus-descriptions.json"],
        BLURB_SETS["depot"]: [SITE / "depot-descriptions.json"],
        BLURB_SETS["waiting"]: [SITE / "waiting-descriptions.json"],
    }
    def size_of(p: Path) -> int:
        d = load_json(p)
        return len(d) if isinstance(d, (dict, list)) else 0

    for src, dests in targets.items():
        if not src.exists():
            continue
        for dest in dests:
            if not dest.parent.exists():
                continue
            src_n, dest_n = size_of(src), size_of(dest)
            # Refuse an unexpectedly large reduction in generated data.
            if dest_n > 0 and src_n < dest_n // 2:
                print(f"REFUSED: {src.name} has {src_n} entries but "
                      f"{dest.relative_to(REPO)} has {dest_n} — not overwriting "
                      f"(delete the destination manually if this is intended)")
                continue
            shutil.copy2(src, dest)
            print(f"distributed {src.name} ({src_n} entries) -> {dest.relative_to(REPO)}")


def main() -> int:
    fix = "--fix" in sys.argv
    if fix:
        try:
            from dotenv import load_dotenv
            load_dotenv(HERE / ".env")
        except ImportError:
            pass
        run_step("fleet refresh (bustimes.org)", ["update_fleet_data.py"])
        if os.getenv("GEMINI_API_KEY"):
            # Seed staging with the current descriptions before generating.
            build_blurb_scope()
            import shutil
            for path in BLURB_SETS.values():
                consumer = SITE / path.name
                if not path.exists() and consumer.exists():
                    shutil.copy2(consumer, path)
                    print(f"seeded staging {path.name} from site/")
            for name, script in GENERATORS.items():
                run_step(f"blurbs: {name} (incremental)", [script])
        else:
            print("\n(skipping blurb generation: GEMINI_API_KEY not set in "
                  "pipeline/.env)")
        gtfs = Path(os.getenv("BBB_GTFS_DIR", HERE / "itm_south_west_gtfs"))
        if (gtfs / "shapes.txt").exists() and os.getenv("BBB_TIMETABLE_DB"):
            run_step("route shapes import", ["import_shapes.py"])
        else:
            print("\n(skipping shapes: need shapes.txt in BBB_GTFS_DIR and "
                  "BBB_TIMETABLE_DB set)")
        distribute()

    report = audit()
    print("\n" + "=" * 60)
    print("ENRICHMENT AUDIT" + ("" if fix else "  (read-only — use --fix to refresh)"))
    print("=" * 60)
    print(json.dumps(report, indent=2))
    (HERE / "enrichment_report.json").write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

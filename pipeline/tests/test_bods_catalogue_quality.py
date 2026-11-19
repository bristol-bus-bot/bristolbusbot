"""#134: per-operator data quality from the BODS catalogue (private)."""
from __future__ import annotations

import csv
import io
import sqlite3
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bods_catalogue_quality as quality  # noqa: E402

FIELDS = ["Organisation Name", "Local Transport Authority", "Scope Status", "Seasonal Status",
          "Requires Attention", "Timetables requires attention", "Timetables Published Status",
          "Timetables Timeliness Status", "AVL requires attention", "AVL Published Status",
          "Error in AVL to Timetable Matching", "Fares requires attention",
          "Registration:Service Number", "Registration:Service Type Description", "TXC:NOC"]


def service(**values):
    row = dict.fromkeys(FIELDS, "No")
    row.update({"Organisation Name": "First Bus", "Local Transport Authority":
                "West of England Combined Authority", "Scope Status": "In Scope",
                "Seasonal Status": "Not Seasonal", "Timetables Published Status": "Published",
                "Timetables Timeliness Status": "Up to date", "AVL Published Status": "Yes",
                "Registration:Service Type Description": "Normal Stopping Service",
                "Registration:Service Number": "1", "TXC:NOC": ""})
    row.update(values)
    return row


def write_csv(rows, fields):
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue()


def test_summary_counts_only_our_area_and_sorts_unpublished_services(tmp_path):
    rows = [
        service(**{"Requires Attention": "Yes", "Timetables requires attention": "Yes",
                   "Timetables Published Status": "Unpublished", "Registration:Service Number": "75"}),
        service(**{"Requires Attention": "Yes", "Timetables requires attention": "Yes",
                   "Timetables Published Status": "Unpublished", "Registration:Service Number": "X99"}),
        service(**{"Timetables Published Status": "Unpublished",
                   "Registration:Service Type Description": "Excursion or Tour",
                   "Registration:Service Number": "City Tour"}),
        service(**{"Local Transport Authority": "North Somerset Council|Somerset County Council",
                   "AVL requires attention": "Yes", "Error in AVL to Timetable Matching": "Yes"}),
        service(**{"Local Transport Authority": "Somerset County Council"}),          # outside
        service(**{"Scope Status": "Out of Scope"}),                                  # out of scope
        service(**{"Seasonal Status": "Out of Season"}),                              # out of season
    ]
    catalogue = tmp_path / "catalogue.zip"
    with zipfile.ZipFile(catalogue, "w") as archive:
        archive.writestr("overall_compliance_report.csv", write_csv(rows, FIELDS))
        archive.writestr("location_data_catalogue.csv", write_csv(
            [{"Organisation Name": "First Bus", "Datafeed ID": "1",
              "% AVL to Timetables feed matching score": "94%"}],
            ["Organisation Name", "Datafeed ID", "% AVL to Timetables feed matching score"]))
    timetable = tmp_path / "timetable.db"
    with sqlite3.connect(timetable) as connection:
        connection.executescript("""
            CREATE TABLE agency (agency_id TEXT, agency_noc TEXT);
            CREATE TABLE routes (route_id TEXT, agency_id TEXT, route_short_name TEXT);
            INSERT INTO agency VALUES ('A', 'FBRI'); INSERT INTO routes VALUES ('R', 'A', '75');
        """)

    report = quality.summarise(catalogue, timetable)

    first = report["operators"]["First Bus"]
    assert first["services"] == 4
    assert first["requires_attention"] == 2 and first["timetable_unpublished"] == 3
    assert first["location_matching_error"] == 1
    assert first["location_matching_score_pct"] == 94.0
    assert first["unpublished_by_kind"] == {
        "route runs in our timetable (registration likely stale or mismatched)": 1,
        "not in our timetable (cancelled or never published)": 1,
        "event, tour, school or works service": 1,
    }
    assert report["total"]["services"] == 4
    assert len(report["caveats"]) == 4

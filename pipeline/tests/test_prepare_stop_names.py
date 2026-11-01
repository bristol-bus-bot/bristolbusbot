import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prepare_stop_names import StopNames, prepare_stop_names  # noqa: E402

NAMES = StopNames()


def test_reviewed_stops_get_their_checked_names():
    assert NAMES.best("bthjdwg", "Sainsbury's") == "Odd Down Sainsbury's"
    assert NAMES.best("wsmpawp", "Sainsbury's") == "Portishead Sainsbury's"
    assert NAMES.best("WSMPGWP", "Public Transport Interchange") == "Bristol Airport Interchange"
    assert NAMES.best("wsmpxxx", "Public Transport Interchange") == "Public Transport Interchange"


def test_generic_stops_say_where_they_are():
    assert NAMES.best("wsmgdpm", "School") == "Tickenham School"
    assert NAMES.best("sglajtp", "The Green") == "The Green, Olveston"


def test_prefixes_and_names_anywhere():
    assert NAMES.best("bstgzz", "Bus Station") == "Bristol Bus Station"
    assert NAMES.best("sglagtzz", "Bus Station") == "Cribbs Causeway Bus Station"
    assert NAMES.best("bthjzzz", "Bus Station") == "Bus Station"
    assert NAMES.best("bstpjgw", "Temple Meads Stn") == "Bristol Temple Meads Station"


def test_abbreviations_are_spelled_out_as_whole_words():
    assert NAMES.best("x", "Bristol Parkway Stn") == "Bristol Parkway Station"
    assert NAMES.best("x", "Briery Leaze Rd") == "Briery Leaze Road"
    assert NAMES.best("x", "Rdway Stnford") == "Rdway Stnford"


def test_the_timetable_keeps_the_published_name_and_reruns_from_it(tmp_path):
    db = tmp_path / "timetable.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE stops (stop_id TEXT PRIMARY KEY, stop_code TEXT, stop_name TEXT)")
    conn.executemany("INSERT INTO stops VALUES (?,?,?)",
                     [("1", "wsmgdpm", "School"), ("2", "x", "High Street"), ("3", None, None)])
    conn.commit(); conn.close()
    assert prepare_stop_names(db) == 1
    assert prepare_stop_names(db) == 1
    rows = sqlite3.connect(db).execute(
        "SELECT stop_id, stop_name, source_stop_name FROM stops ORDER BY stop_id").fetchall()
    assert rows == [("1", "Tickenham School", "School"), ("2", "High Street", "High Street"), ("3", None, None)]


# Carried over from the site, which used to correct names itself.
LOCATION_CASES = json.loads(
    (Path(__file__).parent / "fixtures_stop_name_locations.json").read_text(encoding="utf-8"))["stops"]


@pytest.mark.parametrize("stop", LOCATION_CASES, ids=lambda s: s["code"])
def test_reviewed_official_stop_locations(stop):
    assert NAMES.best(stop["code"], stop["name"]) == stop["expected"]
    assert stop["locality"] in stop["expected"]
    assert NAMES.best(stop["code"], "New stop name") == "New stop name"


@pytest.mark.parametrize("name,code", [
    ("Morrisons", "sglmtxx"), ("Morrisons", "sglpmxx"),
    ("Public Transport Interchange", "wsmpxxx"), ("Tesco", "bthpxxx"),
    ("Tesco", "wsmdpxx"), ("Sainsburys", "sglatxx"), ("Shopping Centre", "sglpwxx"),
])
def test_unreviewed_codes_do_not_invent_a_town(name, code):
    assert NAMES.best(code, name) == name


@pytest.mark.parametrize("code", ["sglmtmg", "sglmtma", "sglmtmd", "sglpwdj", "sglpwdm"])
def test_yate_shopping_centre(code):
    assert NAMES.best(code, "Shopping Centre") == "Yate Shopping Centre"
    assert NAMES.best(code.upper(), "Shopping Centre") == "Yate Shopping Centre"

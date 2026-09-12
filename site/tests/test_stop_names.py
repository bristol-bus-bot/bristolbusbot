import pytest
import json
from pathlib import Path

from app.services.stop_names import clean_stop_name

LOCATION_CASES = json.loads((Path(__file__).parent / "fixtures/stop_name_locations.json").read_text())["stops"]


@pytest.mark.parametrize("stop", LOCATION_CASES, ids=lambda s: s["code"])
def test_reviewed_official_stop_locations(stop):
    assert clean_stop_name(stop["name"], stop["code"]) == stop["expected"]
    assert stop["locality"] in stop["expected"]
    assert clean_stop_name("New stop name", stop["code"]) == "New stop name"


@pytest.mark.parametrize("name,code", [
    ("Morrisons", "sglmtxx"), ("Morrisons", "sglpmxx"),
    ("Public Transport Interchange", "wsmpxxx"), ("Tesco", "bthpxxx"),
    ("Tesco", "wsmdpxx"), ("Sainsburys", "sglatxx"),
])
def test_unreviewed_codes_do_not_invent_a_town(name, code):
    assert clean_stop_name(name, code) == name


@pytest.mark.parametrize("code", ["sglmtmg", "sglmtma", "sglmtmd", "sglpwdj", "sglpwdm"])
def test_yate_shopping_centre(code):
    assert clean_stop_name("Shopping Centre", code) == "Yate Shopping Centre"
    assert clean_stop_name("Shopping Centre", code.upper()) == "Yate Shopping Centre"
    assert clean_stop_name("Renamed Stop", code) == "Renamed Stop"


def test_unknown_shopping_centre_does_not_infer_thornbury():
    for code in ("sglmtxx", "sglpmxx", "sglpwxx"):
        assert clean_stop_name("Shopping Centre", code) == "Shopping Centre"

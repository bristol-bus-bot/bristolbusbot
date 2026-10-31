import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stop_places  # noqa: E402


def test_the_town_goes_after_a_stop_name_unless_the_name_already_says_it(tmp_path):
    path = tmp_path / "stop_enrichment.json"
    path.write_text(json.dumps({
        "nsmtri": {"name": "Triangle", "locality": "Clevedon"},
        "nsmcle": {"name": "Clevedon Triangle", "locality": "Clevedon"},
        "bstnone": {"name": "Somewhere"},
    }), encoding="utf-8")
    towns = stop_places.load(path)
    assert stop_places.place(towns, "nsmtri", "Triangle") == "Clevedon"
    assert stop_places.place(towns, "nsmcle", "Clevedon Triangle") is None
    assert stop_places.place(towns, "bstnone", "Somewhere") is None
    assert stop_places.place(towns, "unknown", "Interchange") is None


def test_a_missing_file_means_no_towns(tmp_path):
    assert stop_places.load(tmp_path / "missing.json") == {}


def test_a_stop_missing_from_the_file_borrows_its_neighbours_town(tmp_path):
    enrichment = tmp_path / "stop_enrichment.json"
    enrichment.write_text(json.dumps({
        "bay1": {"locality": "Bath City Centre"}, "bay2": {"locality": "Bath City Centre"},
        "other": {"locality": "Twerton"},
    }), encoding="utf-8")
    localities = tmp_path / "stop_localities.json"
    localities.write_text(json.dumps({
        "bay1": {"stop_name": "Bus Station", "ward_name": "Kingsmead"},
        "bay2": {"stop_name": "Bus Station", "ward_name": "Kingsmead"},
        "bay3": {"stop_name": "Bus Station", "ward_name": "Kingsmead"},
        "other": {"stop_name": "High Street", "ward_name": "Twerton"},
        "new": {"stop_name": "New Stop", "ward_name": "Twerton"},
        "lost": {"stop_name": "Lost", "ward_name": None},
    }), encoding="utf-8")
    towns = stop_places.load(enrichment, localities)
    assert towns["bay3"] == "Bath City Centre"
    assert towns["new"] == "Twerton"
    assert "lost" not in towns

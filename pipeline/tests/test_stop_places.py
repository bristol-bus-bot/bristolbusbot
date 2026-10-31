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

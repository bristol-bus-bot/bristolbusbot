"""The shared vehicle identity examples, also run by the bot, map and audit check."""
import json
from pathlib import Path

import pytest

from app.services.fleet import Fleet

SHARED = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "vehicle_identity.json")
    .read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fleet(tmp_path_factory):
    path = tmp_path_factory.mktemp("fleet") / "fleet.json"
    path.write_text(json.dumps(SHARED["fleet"]), encoding="utf-8")
    return Fleet(str(path))


@pytest.mark.parametrize("case", SHARED["cases"], ids=lambda c: c["why"])
def test_shared_vehicle_identity(fleet, case):
    found = fleet._record(case["vehicle_ref"], case["operator"])
    expected = case["expected"]
    assert found == (None if expected is None else SHARED["fleet"][expected])

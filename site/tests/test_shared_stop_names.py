"""The shared stop-name examples, also run by the bot's tests."""
import json
from pathlib import Path

import pytest

from app.services.stop_names import (
    STOP_NAME_ANY, STOP_NAME_EXACT, STOP_NAME_PREFIX, clean_stop_name,
)

SHARED = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "fixtures" / "stop_names.json")
    .read_text(encoding="utf-8"))


def test_the_site_holds_exactly_the_shared_stop_name_corrections():
    assert sorted([c, n, v] for (c, n), v in STOP_NAME_EXACT.items()) == SHARED["tables"]["exact"]
    assert [list(rule) for rule in STOP_NAME_PREFIX] == SHARED["tables"]["prefix"]
    assert sorted([n, v] for n, v in STOP_NAME_ANY.items()) == SHARED["tables"]["any"]


@pytest.mark.parametrize("case", SHARED["cases"], ids=lambda c: f"{c['code']}-{c['name']}")
def test_shared_stop_name(case):
    assert clean_stop_name(case["name"], case["code"]) == case["expected"]

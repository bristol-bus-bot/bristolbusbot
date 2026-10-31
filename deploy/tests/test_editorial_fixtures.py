"""The Pi's validator and the bot's must agree on every shared example."""
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "deploy"))

from editorial_context import (  # noqa: E402
    ALLOWED_SOURCE_HOSTS, EditorialValidationError, MAX_OCCASION_USES_PER_DAY, validate_bytes)

FIXTURES = REPO / "shared" / "fixtures" / "editorial_context"
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
BOT_SOURCE = (REPO / "bot" / "src" / "services" / "editorial-context.ts").read_text(encoding="utf-8")


@pytest.mark.parametrize("case", CASES, ids=[case["file"] for case in CASES])
def test_shared_examples(case):
    raw = (FIXTURES / case["file"]).read_bytes()
    if case["valid"]:
        validate_bytes(raw)
    else:
        with pytest.raises(EditorialValidationError):
            validate_bytes(raw)


def test_both_validators_allow_the_same_source_hosts():
    block = re.search(r"const ALLOWED_SOURCE_HOSTS = \[(.*?)\];", BOT_SOURCE, re.S).group(1)
    bot_hosts = set(re.findall(r"'([a-z0-9.-]+)'", block))
    assert bot_hosts == set(ALLOWED_SOURCE_HOSTS)


def test_both_validators_share_the_daily_occasion_cap():
    bot_cap = int(re.search(r"MAX_OCCASION_USES_PER_DAY = (\d+);", BOT_SOURCE).group(1))
    assert bot_cap == MAX_OCCASION_USES_PER_DAY == 8

import importlib.util
import os
import stat
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "configure_collector_health.py"
SPEC = importlib.util.spec_from_file_location("configure_collector_health", SCRIPT)
assert SPEC and SPEC.loader
configure_collector_health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_collector_health)


def test_validate_healthcheck_url_requires_base_ping_url():
    assert configure_collector_health.validate_healthcheck_url(
        "https://hc-ping.com/example_UUID-123/"
    ) == "https://hc-ping.com/example_UUID-123"
    with pytest.raises(ValueError):
        configure_collector_health.validate_healthcheck_url(
            "https://hc-ping.com/example_UUID-123/start"
        )


def test_update_env_text_preserves_other_values_and_removes_duplicates():
    existing = (
        "BODS_API_KEY=do-not-touch\n"
        "BBB_COLLECTOR_HEALTHCHECK_URL=old\n"
        "BBB_TZ=Europe/London\n"
        "BBB_COLLECTOR_HEALTHCHECK_URL=duplicate\n"
    )
    updated = configure_collector_health.update_env_text(
        existing, "https://hc-ping.com/new-check")

    assert "BODS_API_KEY=do-not-touch" in updated
    assert "BBB_TZ=Europe/London" in updated
    assert updated.count("BBB_COLLECTOR_HEALTHCHECK_URL=") == 1
    assert "BBB_COLLECTOR_HEALTHCHECK_URL=https://hc-ping.com/new-check" in updated


def test_private_atomic_write_keeps_mode_0600(tmp_path: Path):
    target = tmp_path / ".env"
    target.write_text("OLD=value\n", encoding="utf-8")
    if os.name != "nt":
        target.chmod(0o600)

    configure_collector_health.write_private_atomic(target, "NEW=value\n")

    assert target.read_text(encoding="utf-8") == "NEW=value\n"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

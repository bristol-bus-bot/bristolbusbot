import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "configure_backup.py"
SPEC = importlib.util.spec_from_file_location("configure_backup", SCRIPT)
assert SPEC and SPEC.loader
configure_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(configure_backup)


def test_validate_r2_endpoint_accepts_default_and_eu_jurisdiction():
    assert configure_backup.validate_r2_endpoint(
        "https://abc123.r2.cloudflarestorage.com/"
    ) == "https://abc123.r2.cloudflarestorage.com"
    assert configure_backup.validate_r2_endpoint(
        "https://abc123.eu.r2.cloudflarestorage.com"
    ) == "https://abc123.eu.r2.cloudflarestorage.com"


@pytest.mark.parametrize(
    "value",
    [
        "http://abc123.r2.cloudflarestorage.com",
        "https://example.com",
        "https://key:secret@abc123.r2.cloudflarestorage.com",
        "https://abc123.r2.cloudflarestorage.com/a-path",
    ],
)
def test_validate_r2_endpoint_rejects_unsafe_values(value):
    with pytest.raises(configure_backup.ConfigurationError):
        configure_backup.validate_r2_endpoint(value)


def test_validate_healthcheck_url_requires_base_ping_url():
    assert configure_backup.validate_healthcheck_url(
        "https://hc-ping.com/example_UUID-123/"
    ) == "https://hc-ping.com/example_UUID-123"
    with pytest.raises(configure_backup.ConfigurationError):
        configure_backup.validate_healthcheck_url(
            "https://hc-ping.com/example_UUID-123/start"
        )


def test_build_config_sets_authorised_mount_and_bucket():
    result = configure_backup.build_config(
        {"expected_mount_source": "placeholder", "r2_repository": "placeholder"},
        "https://account.eu.r2.cloudflarestorage.com",
        "deadbeef-dead-4bad-8dad-deadbeef0001",
        "/srv/darkplace",
    )
    assert result["expected_mount_source"] == (
        "/dev/disk/by-uuid/deadbeef-dead-4bad-8dad-deadbeef0001"
    )
    assert result["r2_repository"] == (
        "s3:https://account.eu.r2.cloudflarestorage.com/"
        "bristolbusbot-backup"
    )


def test_private_outputs_contain_expected_values_and_are_private_on_posix(tmp_path):
    config_path = tmp_path / "backup.json"
    env_path = tmp_path / "backup.env"
    configure_backup.private_write(
        config_path, json.dumps({"safe": "configuration"}) + "\n"
    )
    configure_backup.private_write(
        env_path,
        configure_backup.render_env(
            "ACCESS-EXAMPLE",
            "SECRET-EXAMPLE",
            "https://hc-ping.com/backup-example",
            "https://hc-ping.com/check-example",
        ),
    )
    if os.name != "nt":
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    env_text = env_path.read_text(encoding="utf-8")
    assert "SECRET-EXAMPLE" in env_text
    assert "AWS_DEFAULT_REGION=auto" in env_text
    assert "XDG_CACHE_HOME=/var/cache/bristolbusbot" in env_text

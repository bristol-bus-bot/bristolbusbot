from pathlib import Path

import pytest

from deploy.local_config import LocalConfigError, parse_env, settings_from


EXAMPLE = Path(__file__).resolve().parents[1] / "local.env.example"


def test_public_example_is_valid_and_obviously_fictional():
    settings = settings_from(parse_env(EXAMPLE))
    assert settings.user == "rickdagless"
    assert settings.host == "darkplace-hospital.local"
    assert str(settings.remote_base).endswith("/rickdagless/bristolbusbot")
    assert "deadbeef" in settings.backup_uuid
    assert "Darkplace" in str(settings.local_gtfs_dir)


def test_rejects_relative_remote_home():
    values = parse_env(EXAMPLE)
    values["BBB_REMOTE_HOME"] = "somewhere/private"
    with pytest.raises(LocalConfigError, match="absolute path"):
        settings_from(values)


def test_rejects_malformed_uuid():
    values = parse_env(EXAMPLE)
    values["BBB_BACKUP_UUID"] = "not-a-drive-uuid"
    with pytest.raises(LocalConfigError, match="canonical UUID"):
        settings_from(values)

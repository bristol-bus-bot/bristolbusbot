from __future__ import annotations

import sys
from pathlib import Path

import pytest


SOCIAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOCIAL))

import social_run  # noqa: E402


ENVIRONMENT = {
    "BBB_SOCIAL_DB": "/var/lib/bristolbusbot/social.db",
    "BBB_SOCIAL_APP_DB": "/var/lib/bristolbusbot/bot/app_data.db",
    "BBB_SOCIAL_AUDIT_DB": "/var/lib/bristolbusbot/collector/audit.db",
    "BBB_SOCIAL_OUTPUT_DIR": "/var/lib/bristolbusbot/social/cards",
    "BBB_SOCIAL_CHANNEL_ID": "C12345678",
    "BBB_SOCIAL_ALLOWED_USER_ID": "U12345678",
}


def test_runner_defaults_to_shadow_until_live_marker_exists(tmp_path):
    marker = tmp_path / "social-live-enabled"
    credential = tmp_path / "slack-token"

    args = social_run.build_args(
        ENVIRONMENT, live_marker=marker, credential=credential)
    assert args[-1] == "--shadow"
    assert str(credential) in args

    marker.write_text("enabled\n", encoding="utf-8")
    args = social_run.build_args(
        ENVIRONMENT, live_marker=marker, credential=credential)
    assert "--shadow" not in args


def test_runner_uses_systemd_credential_directory(tmp_path):
    environment = {
        **ENVIRONMENT,
        "CREDENTIALS_DIRECTORY": str(tmp_path / "unit-credentials"),
    }
    args = social_run.build_args(
        environment, live_marker=tmp_path / "missing-marker")
    assert str(tmp_path / "unit-credentials" / "slack-token") in args


def test_runner_refuses_incomplete_configuration(tmp_path):
    with pytest.raises(RuntimeError, match="BBB_SOCIAL_CHANNEL_ID"):
        social_run.build_args(
            {key: value for key, value in ENVIRONMENT.items()
             if key != "BBB_SOCIAL_CHANNEL_ID"},
            live_marker=tmp_path / "marker",
            credential=tmp_path / "token")

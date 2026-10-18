import os
import stat
import sys
from datetime import date
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from configure_timetable_delivery import (
    ConfigurationError,
    private_write,
    render_env,
    validate_expiry,
    validate_token,
)


def test_token_and_expiry_are_validated_without_echoing_secret():
    token = "fine-grained-value-with-enough-characters"
    assert validate_token(token) == token
    assert validate_expiry("2027-01-02", today=date(2026, 7, 22)) == \
        "2027-01-02T00:00:00Z"
    rendered = render_env("2027-01-02T00:00:00Z")
    assert "BBB_GITHUB_TOKEN=" not in rendered
    assert "BBB_GITHUB_TOKEN_EXPIRES_UTC=2027-01-02T00:00:00Z" in rendered
    with pytest.raises(ConfigurationError, match="unsafe format"):
        validate_token("short")
    with pytest.raises(ConfigurationError, match="future"):
        validate_expiry("2026-07-22", today=date(2026, 7, 22))


def test_private_writer_refuses_accidental_overwrite_and_symlink(tmp_path):
    target = tmp_path / "delivery.env"
    private_write(target, "TOKEN=first\n", replace=False)
    assert target.read_text(encoding="utf-8") == "TOKEN=first\n"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600
    with pytest.raises(ConfigurationError, match="already exists"):
        private_write(target, "TOKEN=second\n", replace=False)
    private_write(target, "TOKEN=second\n", replace=True)
    assert target.read_text(encoding="utf-8") == "TOKEN=second\n"

    link = tmp_path / "link.env"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("test account cannot create Windows symlinks")
    with pytest.raises(ConfigurationError, match="regular file"):
        private_write(link, "TOKEN=no\n", replace=True)


def test_expiry_record_is_readable_but_token_stays_private(tmp_path):
    record = tmp_path / "delivery.env"
    private_write(record, "BBB_GITHUB_TOKEN_EXPIRES_UTC=2027-03-29T00:00:00Z\n",
                  replace=False, mode=0o644)
    token = tmp_path / "delivery.token"
    private_write(token, "secret-token-value\n", replace=False)
    if os.name != "nt":
        assert stat.S_IMODE(record.stat().st_mode) == 0o644
        assert stat.S_IMODE(token.stat().st_mode) == 0o600

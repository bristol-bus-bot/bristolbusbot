from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import configure_social_curation as social_config  # noqa: E402


def test_writes_fixed_paths_ids_and_private_token(tmp_path):
    environment, token = social_config.configure(
        tmp_path, "C12345678", "U12345678", "xoxb-test-token-123456789")

    text = environment.read_text(encoding="utf-8")
    for key, value in social_config.FIXED_VALUES.items():
        assert f"{key}={value}\n" in text
    assert "BBB_SOCIAL_CHANNEL_ID=C12345678\n" in text
    assert "BBB_SOCIAL_ALLOWED_USER_ID=U12345678\n" in text
    assert "xoxb-" not in text
    assert token.read_text(encoding="utf-8") == "xoxb-test-token-123456789\n"
    if os.name != "nt":
        assert stat.S_IMODE(environment.stat().st_mode) & 0o027 == 0
        assert stat.S_IMODE(token.stat().st_mode) & 0o077 == 0


def test_refuses_bad_ids_tokens_and_implicit_replacement(tmp_path):
    with pytest.raises(ValueError, match="channel ID"):
        social_config.configure(
            tmp_path, "general", "U12345678", "xoxb-test-token-123456789")
    with pytest.raises(ValueError, match="bot token"):
        social_config.configure(
            tmp_path, "C12345678", "U12345678", "incoming-webhook")

    social_config.configure(
        tmp_path, "C12345678", "U12345678", "xoxb-test-token-123456789")
    with pytest.raises(RuntimeError, match="already exists"):
        social_config.configure(
            tmp_path, "C12345678", "U12345678", "xoxb-new-token-123456789")

    social_config.configure(
        tmp_path, "C12345678", "U12345678", "xoxb-new-token-123456789",
        replace=True)
    assert (tmp_path / "social-slack.token").read_text(
        encoding="utf-8") == "xoxb-new-token-123456789\n"

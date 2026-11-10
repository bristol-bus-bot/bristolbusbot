from pathlib import Path

import pytest

from deploy import configure_blurb_generation as config


def test_values_accepts_dotenv_quoted_ai_configuration(tmp_path: Path):
    source = tmp_path / "bot.env"
    placeholder = "not.a.real.value-" * 2
    source.write_text(
        f'AI_API_KEY="{placeholder}"\n'
        "AI_MODEL='gemini-3.6-flash'\n",
        encoding="utf-8",
    )

    parsed = config.values(source)

    assert parsed["AI_API_KEY"] == placeholder
    assert parsed["AI_MODEL"] == "gemini-3.6-flash"


def test_values_rejects_unterminated_dotenv_quote(tmp_path: Path):
    source = tmp_path / "bot.env"
    source.write_text('AI_API_KEY="unterminated\n', encoding="utf-8")

    with pytest.raises(config.ConfigError, match="malformed quotes"):
        config.values(source)


def _posix_stand_ins(monkeypatch):
    import os
    import sys
    import types
    monkeypatch.setitem(sys.modules, "pwd", types.SimpleNamespace(
        getpwnam=lambda _name: types.SimpleNamespace(pw_gid=0)))
    monkeypatch.setattr(os, "chown", lambda *_args: None, raising=False)
    monkeypatch.setattr(config, "validate_existing", lambda *_args: None)


def test_sync_model_changes_only_the_model_line(tmp_path: Path, monkeypatch):
    """After the bot moves model, the description jobs follow only when asked (#110)."""
    _posix_stand_ins(monkeypatch)
    bot = tmp_path / "bot.env"
    bot.write_text("AI_MODEL=gemini-9.9-flash\n", encoding="utf-8")
    blurb = tmp_path / "blurb.env"
    original = ["GEMINI_API_KEY=placeholder-placeholder-x", "BLURB_GEMINI_MODEL=gemini-3.6-flash",
                "BLURB_MAX_REQUESTS_PER_RUN=3", "AI_PROVIDER=agent-platform"]
    blurb.write_text("\n".join(original) + "\n", encoding="utf-8")

    assert config.sync_model(bot_env=bot, output=blurb, owner="bot") == "model updated"
    lines = blurb.read_text(encoding="utf-8").splitlines()
    assert lines == [original[0], "BLURB_GEMINI_MODEL=gemini-9.9-flash", *original[2:]]
    assert config.sync_model(bot_env=bot, output=blurb, owner="bot") == "unchanged"


def test_sync_model_refuses_a_malformed_model(tmp_path: Path, monkeypatch):
    _posix_stand_ins(monkeypatch)
    bot = tmp_path / "bot.env"
    bot.write_text("AI_MODEL=bad model; rm -rf\n", encoding="utf-8")
    blurb = tmp_path / "blurb.env"
    blurb.write_text("BLURB_GEMINI_MODEL=gemini-3.6-flash\n", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.sync_model(bot_env=bot, output=blurb, owner="bot")
    assert blurb.read_text(encoding="utf-8") == "BLURB_GEMINI_MODEL=gemini-3.6-flash\n"


def test_description_model_defaults_are_named_and_match_the_bot():
    """No moving "-latest" alias anywhere, and one default across bot and jobs."""
    import re
    repo = Path(__file__).resolve().parents[2]
    bot_config = (repo / "bot/src/config/app-config.ts").read_text(encoding="utf-8")
    bot_default = re.search(r"model: process\.env\.AI_MODEL \|\| '([^']+)'", bot_config).group(1)
    automation = (repo / "deploy/blurb_automation.py").read_text(encoding="utf-8")
    job_default = re.search(r'^DEFAULT_BLURB_MODEL = "([^"]+)"', automation, re.M).group(1)
    assert config.DEFAULT_BLURB_MODEL == job_default == bot_default
    moving_alias = re.compile(r"""["'][A-Za-z0-9._-]+-latest["']""")
    for path in (repo / "deploy").glob("*.py"):
        assert not moving_alias.search(path.read_text(encoding="utf-8")), path.name

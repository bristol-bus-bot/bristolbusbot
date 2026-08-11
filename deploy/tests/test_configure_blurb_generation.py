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

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import validate_production_config as config


def write_env(root: Path, component: str, extra: dict[str, str] | None = None) -> None:
    values = dict(config.EXPECTED[component])
    values.update({key: "x" * minimum
                   for key, minimum in config.SECRET_MINIMUMS.get(component, {}).items()})
    values.update(extra or {})
    (root / f"{component}.env").write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")


def test_validates_canonical_settings_without_returning_values(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "validate_private_file", lambda path: None)
    for component in ("collector", "site", "bot"):
        write_env(tmp_path, component)
        assert config.validate(component, tmp_path) is None
    assert config.validate("pipeline", tmp_path) is None


def test_failure_never_exposes_secret_value(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "validate_private_file", lambda path: None)
    secret = "do-not-print-this-secret-value"
    write_env(tmp_path, "bot", {"API_AUTH_TOKEN": secret, "TEST_MODE": "true"})
    with pytest.raises(RuntimeError) as caught:
        config.validate("bot", tmp_path)
    assert secret not in str(caught.value)
    assert "values hidden" in str(caught.value)


def test_can_validate_a_root_owned_candidate_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "validate_private_file", lambda path: None)
    write_env(tmp_path, "bot")
    candidate = tmp_path / "candidate.env"
    (tmp_path / "bot.env").replace(candidate)
    assert config.validate("bot", tmp_path, candidate) is None

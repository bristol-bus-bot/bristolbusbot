import json
import sys
from pathlib import Path

import pytest


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_fleet  # noqa: E402


def test_operator_scoped_and_fallback_fleet_keys(tmp_path):
    source = tmp_path / "fleet.json"
    source.write_text(
        json.dumps([
            {
                "fleet_code": "39461",
                "operator": {"id": "FBRI"},
                "vehicle_type": {
                    "name": "Scania Enviro400 City",
                    "electric": False,
                    "fuel": "Gas",
                },
            }
        ]),
        encoding="utf-8",
    )

    index = audit_fleet.load_fleet_index(source)

    assert index[("FBRI", "39461")]["model"] == "Scania Enviro400 City"
    assert index[("*", "39461")]["fuel"] == "Gas"


def test_missing_or_invalid_fleet_data_is_a_hard_failure(tmp_path):
    with pytest.raises(RuntimeError, match="required audit fleet data"):
        audit_fleet.load_fleet_index(tmp_path / "missing.json")

    empty = tmp_path / "empty.json"
    empty.write_text("[]", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty or invalid"):
        audit_fleet.load_fleet_index(empty)


def test_durable_and_production_fallbacks_follow_development_copies(
        monkeypatch, tmp_path):
    release = tmp_path / "release.json"
    repository = tmp_path / "repository.json"
    durable = tmp_path / "durable.json"
    production = tmp_path / "production.json"
    production_bot = tmp_path / "production-bot.json"
    for path in (release, repository, durable, production, production_bot):
        path.write_text("[]", encoding="utf-8")
    monkeypatch.delenv("BBB_FLEET_FILE", raising=False)
    monkeypatch.setattr(audit_fleet, "FLEET_FILE", release)
    monkeypatch.setattr(audit_fleet, "REPO_FLEET_FILE", repository)
    monkeypatch.setattr(audit_fleet, "DURABLE_FLEET_FILE", durable)
    monkeypatch.setattr(audit_fleet, "PRODUCTION_FLEET_FILE", production)
    monkeypatch.setattr(
        audit_fleet, "PRODUCTION_BOT_FLEET_FILE", production_bot)

    assert audit_fleet.fleet_path() == release
    release.unlink()
    assert audit_fleet.fleet_path() == repository
    repository.unlink()
    assert audit_fleet.fleet_path() == durable
    durable.unlink()
    assert audit_fleet.fleet_path() == production
    production.unlink()
    assert audit_fleet.fleet_path() == production_bot


def test_configured_fleet_path_overrides_all_fallbacks(monkeypatch, tmp_path):
    configured = tmp_path / "configured.json"
    monkeypatch.setenv("BBB_FLEET_FILE", str(configured))

    assert audit_fleet.fleet_path() == configured

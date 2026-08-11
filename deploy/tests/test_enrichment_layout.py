import json
import os
import sys
from pathlib import Path

import pytest


DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))

import enrichment_layout


def payloads(marker: str = "source") -> dict[str, object]:
    return {
        "fbribuses.json": [{"fleet_code": marker}],
        "stop_localities.json": {"stop": {"name": marker}},
        "stop_enrichment.json": {"stop": {"name": marker}},
        "local_flavour.json": {"place": {"flavour": marker}},
        "route_details.json": {"route": {"name": marker}},
        "bus-descriptions.json": {"101": marker},
        "waiting-descriptions.json": {"101": marker},
        "depot-descriptions.json": {"101": marker},
        "model-context.json": {"Known model": marker * 20},
    }


def write_payloads(directory: Path, values: dict[str, object]) -> None:
    directory.mkdir()
    for name, value in values.items():
        (directory / name).write_text(json.dumps(value), encoding="utf-8")


def test_seed_is_atomic_and_never_overwrites_durable_authority(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "durable"
    write_payloads(source, payloads())
    destination.mkdir()
    existing = destination / "route_details.json"
    existing.write_text(json.dumps({"route": {"name": "durable"}}),
                        encoding="utf-8")

    result = enrichment_layout.seed_missing(
        source, destination, uid=os.getuid() if hasattr(os, "getuid") else 0,
        gid=os.getgid() if hasattr(os, "getgid") else 0)

    assert result["route_details.json"] == "preserved"
    assert json.loads(existing.read_text())["route"]["name"] == "durable"
    assert set(enrichment_layout.validate_directory(destination)) == set(
        enrichment_layout.SPECS)
    assert not list(destination.glob(".*.migration"))


def test_invalid_or_empty_source_fails_closed(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "durable"
    write_payloads(source, payloads())
    destination.mkdir()
    (source / "local_flavour.json").write_text("{}", encoding="utf-8")

    with pytest.raises(enrichment_layout.EnrichmentLayoutError,
                       match="wrong shape or is empty"):
        enrichment_layout.seed_missing(
            source, destination,
            uid=os.getuid() if hasattr(os, "getuid") else 0,
            gid=os.getgid() if hasattr(os, "getgid") else 0)


def test_multiple_sources_seed_one_durable_contract(tmp_path):
    bot_source = tmp_path / "bot"
    site_source = tmp_path / "site"
    context_source = tmp_path / "context"
    destination = tmp_path / "durable"
    values = payloads()
    write_payloads(bot_source, {
        name: value for name, value in values.items()
        if name in {"fbribuses.json", "stop_localities.json",
                    "stop_enrichment.json", "local_flavour.json",
                    "route_details.json"}
    })
    write_payloads(site_source, {
        name: value for name, value in values.items()
        if name.endswith("-descriptions.json")
        or name == "bus-descriptions.json"
    })
    write_payloads(context_source, {
        "model-context.json": values["model-context.json"]})
    destination.mkdir()

    result = enrichment_layout.seed_missing(
        [bot_source, site_source, context_source], destination,
        uid=os.getuid() if hasattr(os, "getuid") else 0,
        gid=os.getgid() if hasattr(os, "getgid") else 0)

    assert set(result) == set(enrichment_layout.SPECS)
    assert set(enrichment_layout.validate_directory(destination)) == set(
        enrichment_layout.SPECS)


def test_reviewed_model_context_sync_is_append_only_and_atomic(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "durable"
    source.mkdir()
    destination.mkdir()
    live = {"Known model": "existing context remains exactly unchanged"}
    candidate = {
        **live,
        "New model": "new reviewed context is added through the installer",
    }
    (destination / "model-context.json").write_text(
        json.dumps(live), encoding="utf-8")
    (source / "model-context.json").write_text(
        json.dumps(candidate), encoding="utf-8")

    result = enrichment_layout.sync_model_context(
        source, destination,
        uid=os.getuid() if hasattr(os, "getuid") else 0,
        gid=os.getgid() if hasattr(os, "getgid") else 0,
    )

    assert result == "updated"
    assert json.loads((destination / "model-context.json").read_text()) \
        == candidate
    assert not list(destination.glob(".*.migration"))


def test_model_context_sync_rejects_removal_or_broad_correction(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "durable"
    source.mkdir()
    destination.mkdir()
    live = {
        f"Model {index}": f"existing reviewed context number {index:02d} stays safe"
        for index in range(22)
    }
    target = destination / "model-context.json"
    target.write_text(json.dumps(live), encoding="utf-8")
    candidate = dict(live)
    candidate.pop("Model 0")
    (source / "model-context.json").write_text(
        json.dumps(candidate), encoding="utf-8")

    with pytest.raises(enrichment_layout.EnrichmentLayoutError,
                       match="would remove"):
        enrichment_layout.sync_model_context(
            source, destination, uid=0, gid=0)

    candidate = {
        key: f"corrected reviewed context number {index:02d} remains safe"
        for index, key in enumerate(live)
    }
    (source / "model-context.json").write_text(
        json.dumps(candidate), encoding="utf-8")
    with pytest.raises(enrichment_layout.EnrichmentLayoutError,
                       match="too many existing entries"):
        enrichment_layout.sync_model_context(
            source, destination, uid=0, gid=0)
    assert json.loads(target.read_text()) == live


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs Windows privilege")
def test_existing_symlink_is_rejected(tmp_path):
    source = tmp_path / "source"
    destination = tmp_path / "durable"
    write_payloads(source, payloads())
    destination.mkdir()
    (destination / "fbribuses.json").symlink_to(source / "fbribuses.json")

    with pytest.raises(enrichment_layout.EnrichmentLayoutError,
                       match="not a regular file"):
        enrichment_layout.seed_missing(
            source, destination,
            uid=os.getuid() if hasattr(os, "getuid") else 0,
            gid=os.getgid() if hasattr(os, "getgid") else 0)


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs Windows privilege")
def test_symlinked_destination_directory_is_rejected_before_writing(tmp_path):
    source = tmp_path / "source"
    real_destination = tmp_path / "real-durable"
    destination = tmp_path / "durable"
    write_payloads(source, payloads())
    real_destination.mkdir()
    destination.symlink_to(real_destination, target_is_directory=True)

    with pytest.raises(enrichment_layout.EnrichmentLayoutError,
                       match="enrichment directory is unsafe"):
        enrichment_layout.seed_missing(
            source, destination,
            uid=os.getuid() if hasattr(os, "getuid") else 0,
            gid=os.getgid() if hasattr(os, "getgid") else 0)
    assert not list(real_destination.iterdir())


def test_backup_include_is_added_and_made_required_idempotently(tmp_path):
    config = tmp_path / "backup.json"
    config.write_text(json.dumps({"paths": []}), encoding="utf-8")

    assert enrichment_layout.ensure_backup_include(config) is True
    assert enrichment_layout.ensure_backup_include(config) is False

    saved = json.loads(config.read_text())
    assert saved["paths"] == [{
        "name": "enrichment-state",
        "path": "/var/lib/bristolbusbot/enrichment",
        "required": True,
    }]


def test_backup_include_upgrades_an_optional_entry(tmp_path):
    config = tmp_path / "backup.json"
    config.write_text(json.dumps({"paths": [{
        "name": "enrichment-state",
        "path": "/var/lib/bristolbusbot/enrichment",
        "required": False,
    }]}), encoding="utf-8")

    assert enrichment_layout.ensure_backup_include(config) is True
    assert json.loads(config.read_text())["paths"][0]["required"] is True

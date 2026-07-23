import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY))

from editorial_context import EditorialValidationError, validate_bytes
from editorial_fetch import REPOSITORY_PATH, stage
from editorial_promote import (
    EditorialPromotionError,
    PromotionConfig,
    promote,
)


CONTEXT_PATH = ROOT / "bot" / "data" / "editorial-context.json"


def valid_raw() -> bytes:
    return CONTEXT_PATH.read_bytes()


def changed_raw() -> bytes:
    value = json.loads(valid_raw())
    value["updated_at"] = "2026-07-23T12:00:00Z"
    value["news"] = []
    return (json.dumps(value, indent=2) + "\n").encode()


def github_response(raw: bytes, blob_sha: str = "a" * 40) -> dict:
    return {
        "type": "file",
        "path": REPOSITORY_PATH,
        "name": "editorial-context.json",
        "sha": blob_sha,
        "encoding": "base64",
        "size": len(raw),
        "content": base64.b64encode(raw).decode(),
    }


def seed_candidate(root: Path, raw: bytes, blob_sha: str = "b" * 40) -> None:
    incoming = root / "incoming"
    incoming.mkdir(parents=True)
    (incoming / "editorial-context.json").write_bytes(raw)
    _, summary = validate_bytes(raw)
    (incoming / "metadata.json").write_text(json.dumps({
        "schema_version": 1,
        "repository": "bristol-bus-bot/bristolbusbot",
        "branch": "main",
        "path": REPOSITORY_PATH,
        "blob_sha": blob_sha,
        "fetched_at": "2026-07-23T12:00:00+00:00",
        "content": summary,
    }), encoding="utf-8")


def test_checked_in_context_passes_both_runtime_contracts():
    document, summary = validate_bytes(valid_raw())
    assert summary["facts"] == 9
    assert summary["occasions"] == 8
    assert summary["news"] == 1
    assert "bee network" not in json.dumps(document).lower()


def test_validator_rejects_unapproved_claim_and_source():
    value = json.loads(valid_raw())
    value["facts"][0]["claim"] = "A Bee Network claim."
    with pytest.raises(EditorialValidationError, match="intentionally prohibited"):
        validate_bytes(json.dumps(value).encode())

    value = json.loads(valid_raw())
    value["facts"][0]["source"]["url"] = "https://example.com/not-approved"
    with pytest.raises(EditorialValidationError, match="allowlisted"):
        validate_bytes(json.dumps(value).encode())


def test_fetch_stages_exact_validated_github_bytes(tmp_path):
    raw = valid_raw()
    record = stage(tmp_path, github_response(raw))
    assert (tmp_path / "incoming" / "editorial-context.json").read_bytes() == raw
    assert record["blob_sha"] == "a" * 40
    assert record["content"]["sha256"] == hashlib.sha256(raw).hexdigest()


def test_promoter_accepts_exact_candidate_and_keeps_one_previous_copy(tmp_path):
    old = valid_raw()
    candidate = changed_raw()
    (tmp_path / "editorial-context.json").write_bytes(old)
    seed_candidate(tmp_path, candidate)
    restarts = []

    code, record = promote(
        PromotionConfig(tmp_path),
        restart=lambda: restarts.append("bot"),
        healthy=lambda expected: expected == hashlib.sha256(candidate).hexdigest(),
    )

    assert code == 0
    assert record["outcome"] == "accepted"
    assert restarts == ["bot"]
    assert (tmp_path / "editorial-context.json").read_bytes() == candidate
    assert (tmp_path / "editorial-context.json.previous").read_bytes() == old
    assert not (tmp_path / "incoming" / "editorial-context.json").exists()


def test_promoter_skips_identical_content_without_restart(tmp_path):
    raw = valid_raw()
    (tmp_path / "editorial-context.json").write_bytes(raw)
    seed_candidate(tmp_path, raw)
    restarts = []
    code, record = promote(
        PromotionConfig(tmp_path),
        restart=lambda: restarts.append("bot"),
        healthy=lambda _expected: True,
    )
    assert code == 75
    assert record["outcome"] == "no_change"
    assert restarts == []


def test_promoter_rolls_back_when_bot_does_not_load_candidate(tmp_path):
    old = valid_raw()
    candidate = changed_raw()
    old_digest = hashlib.sha256(old).hexdigest()
    (tmp_path / "editorial-context.json").write_bytes(old)
    seed_candidate(tmp_path, candidate)
    restarts = []

    with pytest.raises(EditorialPromotionError, match="rolled back"):
        promote(
            PromotionConfig(tmp_path),
            restart=lambda: restarts.append("bot"),
            healthy=lambda expected: expected == old_digest,
        )

    assert restarts == ["bot", "bot"]
    assert (tmp_path / "editorial-context.json").read_bytes() == old
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["outcome"] == "rolled_back"
    assert state["recovery_healthy"] is True

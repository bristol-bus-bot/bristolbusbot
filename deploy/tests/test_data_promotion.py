import hashlib
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
sys.path.insert(0, str(DEPLOY))

from data_promotion import (
    NO_CHANGE,
    ArtifactContract,
    DataPromotionError,
    promote,
)


def encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def validator(raw: bytes) -> dict[str, object]:
    value = json.loads(raw)
    if not isinstance(value, dict) or not value:
        raise ValueError("expected a non-empty object")
    return {"records": len(value)}


def contract(root: Path, maximum_bytes: int = 16 * 1024) -> ArtifactContract:
    incoming = root / "incoming"
    incoming.mkdir(exist_ok=True)
    return ArtifactContract(
        name="fixture",
        live=root / "fixture.json",
        candidate=incoming / "fixture.json",
        previous=root / "fixture.json.previous",
        state=root / "fixture-promotion.json",
        maximum_bytes=maximum_bytes,
    )


def seed(root: Path) -> tuple[ArtifactContract, bytes, bytes]:
    details = contract(root)
    old = encoded({"old": True})
    candidate = encoded({"new": True, "records": [1, 2]})
    details.live.write_bytes(old)
    details.candidate.write_bytes(candidate)
    return details, old, candidate


def read_state(details: ArtifactContract) -> dict:
    return json.loads(details.state.read_text(encoding="utf-8"))


def test_accepts_valid_candidate_and_keeps_previous_copy(tmp_path):
    details, old, candidate = seed(tmp_path)
    restarts = []

    code, record = promote(
        details,
        validate=validator,
        restart=lambda: restarts.append("consumers"),
        healthy=lambda expected, summary: (
            expected == hashlib.sha256(candidate).hexdigest()
            and summary["records"] == 2
        ),
        context={"source": "fixture-test"},
    )

    assert code == 0
    assert record["outcome"] == "accepted"
    assert record["context"] == {"source": "fixture-test"}
    assert restarts == ["consumers"]
    assert details.live.read_bytes() == candidate
    assert details.previous.read_bytes() == old
    assert not details.candidate.exists()
    assert read_state(details)["candidate"]["bytes"] == len(candidate)


def test_skips_identical_candidate_without_restart(tmp_path):
    details = contract(tmp_path)
    raw = encoded({"same": True})
    details.live.write_bytes(raw)
    details.candidate.write_bytes(raw)
    restarts = []

    code, record = promote(
        details,
        validate=validator,
        restart=lambda: restarts.append("consumers"),
        healthy=lambda _expected, _summary: True,
    )

    assert code == NO_CHANGE
    assert record["outcome"] == "no_change"
    assert restarts == []
    assert not details.previous.exists()
    assert not details.candidate.exists()


@pytest.mark.parametrize("candidate", [b"not-json", b"{}\n"])
def test_rejects_invalid_candidate_without_changing_live(tmp_path, candidate):
    details = contract(tmp_path)
    old = encoded({"old": True})
    details.live.write_bytes(old)
    details.candidate.write_bytes(candidate)

    with pytest.raises(DataPromotionError, match="candidate artifact failed"):
        promote(
            details,
            validate=validator,
            restart=lambda: pytest.fail("restart must not run"),
            healthy=lambda _expected, _summary: True,
        )

    assert details.live.read_bytes() == old
    assert details.candidate.read_bytes() == candidate
    assert not details.previous.exists()
    assert not details.state.exists()


def test_rejects_candidate_over_the_code_owned_size_limit(tmp_path):
    details = contract(tmp_path, maximum_bytes=10)
    old = b'{"x": 1}\n'
    details.live.write_bytes(old)
    details.candidate.write_bytes(b'{"candidate": true}\n')

    with pytest.raises(DataPromotionError, match="candidate artifact is missing"):
        promote(
            details,
            validate=validator,
            restart=lambda: None,
            healthy=lambda _expected, _summary: True,
        )

    assert details.live.read_bytes() == old


def test_rolls_back_when_promoted_digest_is_not_healthy(tmp_path):
    details, old, candidate = seed(tmp_path)
    old_digest = hashlib.sha256(old).hexdigest()
    restarts = []

    with pytest.raises(DataPromotionError, match="rolled back"):
        promote(
            details,
            validate=validator,
            restart=lambda: restarts.append("consumers"),
            healthy=lambda expected, _summary: expected == old_digest,
        )

    assert restarts == ["consumers", "consumers"]
    assert details.live.read_bytes() == old
    assert details.previous.read_bytes() == old
    assert not details.candidate.exists()
    state = read_state(details)
    assert state["outcome"] == "rolled_back"
    assert state["candidate"]["sha256"] == hashlib.sha256(candidate).hexdigest()
    assert state["recovery_healthy"] is True


def test_names_failed_rollback_health_gate(tmp_path):
    details, old, _candidate = seed(tmp_path)

    with pytest.raises(DataPromotionError, match="both failed"):
        promote(
            details,
            validate=validator,
            restart=lambda: None,
            healthy=lambda _expected, _summary: False,
        )

    assert details.live.read_bytes() == old
    state = read_state(details)
    assert state["outcome"] == "rollback_failed"
    assert state["recovery_healthy"] is False


def test_names_failure_before_live_replace_and_keeps_candidate(tmp_path):
    details, old, candidate = seed(tmp_path)

    def fail(point: str) -> None:
        if point == "after_previous":
            raise RuntimeError("injected pre-replace failure")

    with pytest.raises(DataPromotionError, match="before live data"):
        promote(
            details,
            validate=validator,
            restart=lambda: pytest.fail("restart must not run"),
            healthy=lambda _expected, _summary: True,
            fault=fail,
        )

    assert details.live.read_bytes() == old
    assert details.candidate.read_bytes() == candidate
    assert read_state(details)["outcome"] == "failed_before_replace"


def test_recovers_interrupted_swap_by_health_gating_live_candidate(tmp_path):
    details, old, candidate = seed(tmp_path)
    candidate_digest = hashlib.sha256(candidate).hexdigest()

    def interrupt(point: str) -> None:
        if point == "after_replace":
            raise SystemExit("simulated power loss")

    with pytest.raises(SystemExit, match="power loss"):
        promote(
            details,
            validate=validator,
            restart=lambda: pytest.fail("restart occurs after injected fault"),
            healthy=lambda _expected, _summary: True,
            fault=interrupt,
        )

    assert details.live.read_bytes() == candidate
    assert details.previous.read_bytes() == old
    assert read_state(details)["outcome"] == "running"
    restarts = []

    code, record = promote(
        details,
        validate=validator,
        restart=lambda: restarts.append("consumers"),
        healthy=lambda expected, _summary: expected == candidate_digest,
    )

    assert code == 0
    assert restarts == ["consumers"]
    assert record["outcome"] == "accepted"
    assert record["recovered_interrupted_transaction"] is True
    assert not details.candidate.exists()


def test_rejects_unrelated_live_file_during_interrupted_recovery(tmp_path):
    details, _old, _candidate = seed(tmp_path)

    def interrupt(point: str) -> None:
        if point == "after_replace":
            raise SystemExit

    with pytest.raises(SystemExit):
        promote(
            details,
            validate=validator,
            restart=lambda: None,
            healthy=lambda _expected, _summary: True,
            fault=interrupt,
        )
    unrelated = encoded({"unrelated": True})
    details.live.write_bytes(unrelated)

    with pytest.raises(DataPromotionError, match="neither side"):
        promote(
            details,
            validate=validator,
            restart=lambda: None,
            healthy=lambda _expected, _summary: True,
        )

    assert details.live.read_bytes() == unrelated


@pytest.mark.skipif(os.name == "nt", reason="Windows symlinks need privileges")
def test_rejects_symlink_candidate(tmp_path):
    details = contract(tmp_path)
    details.live.write_bytes(encoded({"old": True}))
    target = tmp_path / "outside.json"
    target.write_bytes(encoded({"new": True}))
    details.candidate.symlink_to(target)

    with pytest.raises(DataPromotionError, match="candidate artifact is missing"):
        promote(
            details,
            validate=validator,
            restart=lambda: None,
            healthy=lambda _expected, _summary: True,
        )

    assert details.live.read_bytes() == encoded({"old": True})

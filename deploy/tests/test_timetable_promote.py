import json
import hashlib
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
PIPELINE = ROOT / "pipeline"
sys.path.insert(0, str(DEPLOY))
sys.path.insert(0, str(PIPELINE))

from test_timetable_control import make_timetable
from test_timetable_delivery import COMMIT, RUN_ID, write_manifest
from timetable_control import paths, validate
from timetable_promote import (
    PromotionConfig,
    PromotionError,
    PromotionSkipped,
    SystemServices,
    TimetablePromoter,
)


class FakeServices:
    def __init__(self, failure: str | None = None):
        self.failure = failure
        self.failed = False
        self.restarts: list[str] = []

    def _fails(self, stage: str) -> bool:
        if self.failure == stage and not self.failed:
            self.failed = True
            return True
        return False

    def restart(self, component: str) -> None:
        self.restarts.append(component)
        if self._fails(f"restart:{component}"):
            raise RuntimeError("injected restart failure")

    def wait_component(self, component: str) -> bool:
        return not self._fails(f"health:{component}")

    def wait_public(self) -> bool:
        return not self._fails("public_health")


def test_collector_health_allows_the_pi_integrity_check_to_finish(monkeypatch):
    calls = []

    def completed(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", completed)

    assert SystemServices().wait_component("collector") is True
    assert calls[0][0] == [
        "/usr/local/libexec/bbb-verify-collector-state", "--max-poll-age", "180"]
    assert calls[0][1]["timeout"] == 45


def test_health_request_has_an_explicit_identity(monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size=-1):
            return b'{"status":"ok"}'

    def open_request(request, **kwargs):
        seen.append((request, kwargs))
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", open_request)

    assert SystemServices._json("https://bristolbuses.live/healthz") == {
        "status": "ok"}
    assert seen[0][0].get_header("User-agent") == \
        "bristolbusbot-timetable-promoter/1"
    assert seen[0][0].get_header("Accept") == "application/json"
    assert seen[0][1]["timeout"] == 10


def test_site_health_gate_exercises_the_real_stop_search_endpoint(monkeypatch):
    calls = []

    def response(url, *, timeout=10):
        calls.append((url, timeout))
        if url.endswith("/healthz"):
            return {"status": "ok"}
        return {"stops": [{}] * 1000}

    monkeypatch.setattr(SystemServices, "_json", staticmethod(response))

    assert SystemServices().wait_component("site") is True
    assert calls == [
        ("http://127.0.0.1:5002/healthz", 10),
        ("http://127.0.0.1:5002/api/stops-with-locality", 20),
    ]


def test_site_health_gate_rejects_an_incomplete_stop_search_payload(monkeypatch):
    def response(url, *, timeout=10):
        if url.endswith("/healthz"):
            return {"status": "ok"}
        return {"stops": []}

    monkeypatch.setattr(SystemServices, "_json", staticmethod(response))
    monkeypatch.setattr("timetable_promote.time.sleep", lambda _seconds: None)

    assert SystemServices().wait_component("site") is False


def test_bot_health_gate_requires_the_nested_timetable_connection(monkeypatch):
    payload = {
        "success": True,
        "runtime": "systemd",
        "details": {
            "healthData": {
                "database": {"timetable": {"connected": True}},
            },
        },
    }
    monkeypatch.setattr(
        SystemServices, "_json", staticmethod(lambda _url, **_kwargs: payload))

    assert SystemServices().wait_component("bot") is True

    payload["details"]["healthData"]["database"]["timetable"]["connected"] = False
    monkeypatch.setattr("timetable_promote.time.sleep", lambda _seconds: None)
    assert SystemServices().wait_component("bot") is False


def promotion_case(tmp_path: Path, *, services=None, fault=None):
    shadow = tmp_path / "shadow"
    candidate = shadow / "candidate"
    candidate.mkdir(parents=True, mode=0o700)
    database = candidate / "timetable.db"
    make_timetable(database, latest="20991231")
    manifest = candidate / "manifest.json"
    write_manifest(database, manifest)
    attribution = candidate / "TIMETABLE_ARTIFACT_ATTRIBUTION.txt"
    attribution.write_text(
        "Contains public sector information licensed under the "
        "Open Government Licence v3.0.\n",
        encoding="utf-8",
    )
    for path in (database, manifest, attribution):
        path.chmod(0o600)
    candidate.chmod(0o700)

    validation = validate(database, minimum_service_days=14)
    delivery_state = shadow / "state.json"
    delivery_state.write_text(json.dumps({
        "schema": 1,
        "last_shadow_run_id": str(RUN_ID),
        "last_shadow_attempt": {
            "outcome": "success",
            "mode": "auto",
            "run_id": RUN_ID,
            "commit": COMMIT,
            "database_sha256": hashlib.sha256(
                database.read_bytes()).hexdigest(),
            "validation": validation,
        },
    }), encoding="utf-8")
    delivery_state.chmod(0o600)

    live_root = tmp_path / "live"
    live_root.mkdir(mode=0o750)
    live = live_root / "timetable.db"
    make_timetable(live, latest="20980101")
    live.chmod(0o600)
    monitoring = tmp_path / "monitoring"
    monitoring.mkdir(mode=0o750)
    marker = tmp_path / "promotion-enabled"
    details = database.stat()
    config = PromotionConfig(
        shadow_root=shadow,
        live_root=live_root,
        monitoring_root=monitoring,
        enable_marker=marker,
        expected_owner="unused-in-tests",
        expected_uid=details.st_uid,
        expected_gid=details.st_gid,
    )
    fake = services or FakeServices()
    promoter = TimetablePromoter(config, fake, fault=fault)
    return promoter, fake, database, live, marker


def attended(candidate: Path) -> str:
    return f"{RUN_ID}-{hashlib.sha256(candidate.read_bytes()).hexdigest()}"


def test_attended_promotion_is_atomic_retains_previous_and_is_idempotent(tmp_path):
    promoter, services, candidate, live, _ = promotion_case(tmp_path)

    result = promoter.run(attended(candidate))

    assert result["outcome"] == "accepted"
    assert validate(live)["latest_service"] == "20991231"
    assert validate(candidate)["latest_service"] == "20991231"
    _, _, previous, _ = paths(promoter.config.live_root)
    assert validate(previous)["latest_service"] == "20980101"
    assert services.restarts == ["collector", "site", "bot"]
    state = json.loads(promoter.config.promotion_state.read_text(encoding="utf-8"))
    assert state["outcome"] == "accepted"
    assert state["last_accepted_run_id"] == str(RUN_ID)

    before_restarts = list(services.restarts)
    assert promoter.run(attended(candidate))["outcome"] == "no_change"
    assert services.restarts == before_restarts


def test_auto_mode_is_structurally_disabled_without_root_marker(tmp_path):
    promoter, services, _, live, _ = promotion_case(tmp_path)
    before = live.read_bytes()

    with pytest.raises(PromotionSkipped) as failure:
        promoter.run("auto")

    assert failure.value.code == "promotion_disabled"
    assert live.read_bytes() == before
    assert services.restarts == []


def test_auto_mode_cannot_consume_an_attended_shadow(tmp_path):
    promoter, services, _, live, marker = promotion_case(tmp_path)
    marker.write_text("enabled\n", encoding="utf-8")
    marker.chmod(0o644)
    state = json.loads(promoter.config.delivery_state.read_text(encoding="utf-8"))
    state["last_shadow_attempt"]["mode"] = "attended"
    promoter.config.delivery_state.write_text(json.dumps(state), encoding="utf-8")
    before = live.read_bytes()

    with pytest.raises(PromotionSkipped) as failure:
        promoter.run("auto")

    assert failure.value.code == "shadow_not_automatic"
    assert live.read_bytes() == before
    assert services.restarts == []


def test_attended_promotion_refuses_a_different_reviewed_identity(tmp_path):
    promoter, _, candidate, _, _ = promotion_case(tmp_path)

    with pytest.raises(PromotionError) as failure:
        promoter.run(f"{RUN_ID + 1}-{hashlib.sha256(candidate.read_bytes()).hexdigest()}")

    assert failure.value.code == "attended_identity_mismatch"
    state = json.loads(promoter.config.promotion_state.read_text(encoding="utf-8"))
    assert state["last_attempt"]["outcome"] == "rejected"


def test_auto_same_accepted_hash_is_a_fast_no_change(tmp_path, monkeypatch):
    promoter, services, candidate, _, marker = promotion_case(tmp_path)
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    marker.write_text("enabled\n", encoding="utf-8")
    marker.chmod(0o644)
    promoter.write_state({
        "outcome": "accepted",
        "finished_at": "2026-07-20T03:12:00+00:00",
        "run_id": "29944744744",
        "commit": COMMIT,
        "database_sha256": digest,
    })
    monkeypatch.setattr(
        promoter, "candidate",
        lambda *_args, **_kwargs: pytest.fail("candidate validation was not needed"))

    result = promoter.run("auto")

    assert result["outcome"] == "no_change"
    assert result["run_id"] == str(RUN_ID)
    assert services.restarts == []


def test_v1_accepted_state_survives_a_pre_candidate_failure(tmp_path):
    promoter, _, _, _, _ = promotion_case(tmp_path)
    old_hash = "c" * 64
    promoter.config.promotion_state.write_text(json.dumps({
        "outcome": "accepted",
        "last_accepted_run_id": "29944744744",
        "last_accepted_at": "2026-07-20T03:12:00+00:00",
        "database_sha256": old_hash,
        "commit": COMMIT,
    }), encoding="utf-8")

    with pytest.raises(PromotionError):
        promoter.run(f"{RUN_ID}-{('0' * 64)}")

    state = json.loads(promoter.config.promotion_state.read_text(encoding="utf-8"))
    assert state["schema"] == 2
    assert state["last_attempt"]["outcome"] == "rejected"
    assert state["last_accepted"]["run_id"] == "29944744744"
    assert state["last_accepted"]["database_sha256"] == old_hash


def test_failure_before_replace_rejects_and_cleans_root_staging(tmp_path):
    def fail(stage: str) -> None:
        if stage == "before_replace":
            raise RuntimeError("injected pre-replace failure")

    promoter, services, candidate, live, _ = promotion_case(tmp_path, fault=fail)
    before = live.read_bytes()

    with pytest.raises(PromotionError) as failure:
        promoter.run(attended(candidate))

    assert failure.value.code == "promotion_failed"
    assert live.read_bytes() == before
    _, upload, previous, _ = paths(promoter.config.live_root)
    assert not upload.exists()
    assert not previous.exists()
    state = json.loads(promoter.config.promotion_state.read_text(encoding="utf-8"))
    assert state["outcome"] == "rejected"
    assert services.restarts == []


@pytest.mark.parametrize("failure_stage", [
    "after_replace",
    "restart:site",
    "health:bot",
    "public_health",
])
def test_post_replace_failures_restore_old_timetable_and_health(
        tmp_path, failure_stage):
    services = FakeServices(
        failure_stage if failure_stage != "after_replace" else None)

    def fault(stage: str) -> None:
        if failure_stage == "after_replace" and stage == "after_replace":
            raise RuntimeError("injected post-replace failure")

    promoter, _, candidate, live, _ = promotion_case(
        tmp_path, services=services, fault=fault)

    with pytest.raises(PromotionError) as failure:
        promoter.run(attended(candidate))

    assert failure.value.code == "rolled_back"
    assert validate(live)["latest_service"] == "20980101"
    _, _, previous, failed = paths(promoter.config.live_root)
    assert not previous.exists()
    assert validate(failed)["latest_service"] == "20991231"
    state = json.loads(promoter.config.promotion_state.read_text(encoding="utf-8"))
    assert state["outcome"] == "rolled_back"
    assert state["recovery_healthy"] is True
    assert services.restarts[-3:] == ["collector", "site", "bot"]


def test_auto_does_not_retry_the_same_rolled_back_candidate(tmp_path):
    services = FakeServices("public_health")
    promoter, _, candidate, live, _ = promotion_case(tmp_path, services=services)

    with pytest.raises(PromotionError) as failure:
        promoter.run(attended(candidate))
    assert failure.value.code == "rolled_back"
    before = live.read_bytes()
    before_restarts = list(services.restarts)
    promoter.auto_enabled = lambda: True

    with pytest.raises(PromotionSkipped) as replay:
        promoter.run("auto")

    assert replay.value.code == "candidate_previously_rejected"
    assert live.read_bytes() == before
    assert services.restarts == before_restarts


@pytest.mark.parametrize("health_fails", [False, True])
def test_interrupted_post_replace_transaction_is_finished_safely(
        tmp_path, health_fails):
    services = FakeServices("public_health" if health_fails else None)
    promoter, _, candidate, live, _ = promotion_case(
        tmp_path, services=services)
    old = live.read_bytes()
    _, _, previous, _ = paths(promoter.config.live_root)
    previous.write_bytes(old)
    previous.chmod(0o600)
    live.write_bytes(candidate.read_bytes())
    live.chmod(0o600)
    promoter.write_state({
        "outcome": "running",
        "run_id": str(RUN_ID),
        "commit": COMMIT,
        "database_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        "previous_sha256": hashlib.sha256(old).hexdigest(),
    })

    if health_fails:
        with pytest.raises(PromotionError) as failure:
            promoter.run(attended(candidate))
        assert failure.value.code == "rolled_back"
        assert live.read_bytes() == old
    else:
        result = promoter.run(attended(candidate))
        assert result["outcome"] == "accepted"
        assert result["recovered_interrupted_transaction"] is True
        assert live.read_bytes() == candidate.read_bytes()
    assert services.restarts[:3] == ["collector", "site", "bot"]

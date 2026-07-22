import hashlib
import json
import shutil
import stat
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
PIPELINE = ROOT / "pipeline"
sys.path.insert(0, str(DEPLOY))
sys.path.insert(0, str(PIPELINE))

from test_timetable_control import make_timetable
from timetable_control import EXPECTED_FBRI, VALIDATOR_ID, validate
from timetable_delivery import (
    ALLOWED_FILES,
    ARTIFACT_NAME,
    DEFAULT_BRANCH,
    DeliveryConfig,
    DeliveryError,
    DeliverySkipped,
    GitHubClient,
    MANIFEST_VERSION,
    REPOSITORY,
    SafeRedirectHandler,
    WORKFLOW_PATH,
    TimetableDelivery,
    compare_with_current,
    extract_safely,
    read_github_token,
    sha256_file,
)
from timetable_manifest import database_summary


NOW = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
RUN_ID = 123456
COMMIT = "a" * 40


def source_record(name: str) -> dict:
    return {
        "name": name,
        "bytes": 100,
        "sha256": "b" * 64,
        "modified_utc": "2026-07-22T08:00:00+00:00",
    }


def run_record(**updates) -> dict:
    value = {
        "id": RUN_ID,
        "workflow_id": 99,
        "head_branch": DEFAULT_BRANCH,
        "event": "workflow_dispatch",
        "path": WORKFLOW_PATH,
        "head_repository": {"full_name": REPOSITORY},
        "head_sha": COMMIT,
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-07-22T09:00:00+00:00",
    }
    value.update(updates)
    return value


def write_manifest(database: Path, destination: Path, **updates) -> None:
    result = validate(database, minimum_service_days=14)
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_utc": "2026-07-22T09:05:00+00:00",
        "build": {
            "started_utc": "2026-07-22T09:00:00+00:00",
            "finished_utc": "2026-07-22T09:05:00+00:00",
        },
        "builder": {
            "commit": COMMIT,
            "workflow_run_id": str(RUN_ID),
            "repository": REPOSITORY,
            "ref": f"refs/heads/{DEFAULT_BRANCH}",
        },
        "artifact": {
            "filename": "timetable.db",
            "bytes": database.stat().st_size,
            "sha256": sha256_file(database),
        },
        "database": database_summary(database),
        "validation": {
            "validator": VALIDATOR_ID,
            "minimum_service_days": 14,
            "result": result,
        },
        "sources": {
            "bods_gtfs": {"files": [
                source_record(name) for name in (
                    "agency.txt", "routes.txt", "stops.txt", "trips.txt",
                    "stop_times.txt", "calendar.txt", "shapes.txt",
                )
            ]},
            "first_txc": {
                "status": "used", "files": [source_record("first.zip")],
            },
            "tnds": {
                "status": "not_needed",
                "missing_before_fallback": [],
                "files": [],
            },
        },
        "licence": {
            "identifier": "OGL-3.0",
            "attribution_file": "TIMETABLE_ARTIFACT_ATTRIBUTION.txt",
        },
    }
    manifest.update(updates)
    destination.write_text(json.dumps(manifest), encoding="utf-8")


def make_package(tmp_path: Path, *, mutate=None) -> Path:
    payload = tmp_path / "payload"
    payload.mkdir(parents=True)
    database = payload / "timetable.db"
    make_timetable(database)
    manifest = payload / "manifest.json"
    write_manifest(database, manifest)
    (payload / "TIMETABLE_ARTIFACT_ATTRIBUTION.txt").write_text(
        "Contains public sector information licensed under the "
        "Open Government Licence v3.0.\n",
        encoding="utf-8",
    )
    if mutate:
        mutate(payload)
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for name in sorted(ALLOWED_FILES):
            package.write(payload / name, name)
    return archive


class FakeClient:
    def __init__(self, archive: Path, run=None):
        self.token = "fine-grained-test-token"
        self.archive = archive
        self.run_value = run or run_record()
        self.calls = []

    def workflow(self):
        self.calls.append("workflow")
        return {"id": 99, "path": WORKFLOW_PATH, "state": "active"}

    def enable_workflow(self):
        self.calls.append("enable")

    def run(self, run_id):
        self.calls.append(("run", run_id))
        return self.run_value

    def runs(self):
        self.calls.append("runs")
        return [self.run_value]

    def dispatch(self):
        self.calls.append("dispatch")

    def artifacts(self, run_id):
        self.calls.append(("artifacts", run_id))
        return [{
            "id": 77,
            "name": ARTIFACT_NAME,
            "expired": False,
            "size_in_bytes": self.archive.stat().st_size,
            "created_at": "2026-07-22T09:06:00+00:00",
            "digest": f"sha256:{sha256_file(self.archive)}",
        }]

    def download(self, artifact_id, destination, expected_digest):
        self.calls.append(("download", artifact_id, expected_digest))
        shutil.copyfile(self.archive, destination)
        return destination.stat().st_size


def delivery(tmp_path: Path, client: FakeClient) -> TimetableDelivery:
    live = tmp_path / "live" / "timetable.db"
    live.parent.mkdir(parents=True)
    make_timetable(live)
    config = DeliveryConfig(
        shadow_root=tmp_path / "shadow",
        live_database=live,
        poll_timeout_seconds=1,
        discovery_timeout_seconds=1,
    )
    return TimetableDelivery(
        config,
        client,
        now=lambda: NOW,
        sleeper=lambda _seconds: None,
        token_expires_utc="2027-01-01T00:00:00+00:00",
    )


def test_attended_shadow_delivery_validates_and_never_addresses_live_output(tmp_path):
    archive = make_package(tmp_path)
    client = FakeClient(archive)
    runner = delivery(tmp_path, client)

    result = runner.run(RUN_ID)

    assert result["run_id"] == RUN_ID
    assert result["tnds_status"] == "not_needed"
    assert {path.name for path in runner.config.candidate_path.iterdir()} == ALLOWED_FILES
    assert validate(runner.config.live_database)["latest_service"] == "20991231"
    state = json.loads(runner.config.state_path.read_text(encoding="utf-8"))
    assert state["last_shadow_run_id"] == str(RUN_ID)
    assert state["last_shadow_attempt"]["outcome"] == "success"
    assert state["token_expires_utc"] == "2027-01-01T00:00:00+00:00"

    with pytest.raises(DeliverySkipped, match="already shadow-delivered"):
        runner.run(RUN_ID)
    assert client.calls.count(("download", 77, f"sha256:{sha256_file(archive)}")) == 1


def test_first_auto_run_refreshes_even_with_far_future_coverage(tmp_path):
    archive = make_package(tmp_path)
    client = FakeClient(archive)
    runner = delivery(tmp_path, client)

    result = runner.run()

    assert result["run_id"] == RUN_ID
    state = json.loads(runner.config.state_path.read_text(encoding="utf-8"))
    assert state["last_check"]["coverage_urgent"] is False
    assert state["last_check"]["refresh_due"] is True


def test_recent_auto_success_skips_without_contacting_github(tmp_path):
    archive = make_package(tmp_path)
    client = FakeClient(archive)
    runner = delivery(tmp_path, client)
    runner.config.shadow_root.mkdir(parents=True)
    runner.config.state_path.write_text(json.dumps({
        "schema": 1,
        "last_shadow_success_at": (NOW - timedelta(days=5)).isoformat(),
    }), encoding="utf-8")

    with pytest.raises(DeliverySkipped, match="recent shadow delivery"):
        runner.run()

    assert client.calls == []
    state = json.loads(runner.config.state_path.read_text(encoding="utf-8"))
    assert state["last_check"]["coverage_urgent"] is False
    assert state["last_check"]["refresh_due"] is False


def test_auto_refreshes_again_after_six_days(tmp_path):
    archive = make_package(tmp_path)
    client = FakeClient(archive)
    runner = delivery(tmp_path, client)
    runner.config.shadow_root.mkdir(parents=True)
    runner.config.state_path.write_text(json.dumps({
        "schema": 1,
        "last_shadow_run_id": "older-run",
        "last_shadow_success_at": (NOW - timedelta(days=6)).isoformat(),
    }), encoding="utf-8")

    result = runner.run()

    assert result["run_id"] == RUN_ID
    assert "runs" in client.calls


def test_bad_manifest_hash_and_validation_leave_live_and_candidate_untouched(tmp_path):
    def corrupt(payload):
        manifest = json.loads((payload / "manifest.json").read_text(encoding="utf-8"))
        manifest["artifact"]["sha256"] = "0" * 64
        (payload / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    archive = make_package(tmp_path, mutate=corrupt)
    runner = delivery(tmp_path, FakeClient(archive))
    live_hash = sha256_file(runner.config.live_database)

    with pytest.raises(DeliveryError, match="SHA-256") as failure:
        runner.run(RUN_ID)
    assert failure.value.code == "candidate_validation_failed"
    assert sha256_file(runner.config.live_database) == live_hash
    assert not runner.config.candidate_path.exists()
    assert not (runner.config.shadow_root / ".incoming").exists()


def test_safe_extractor_rejects_extra_files_and_symlinks(tmp_path):
    archive = tmp_path / "extra.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for name in ALLOWED_FILES:
            package.writestr(name, b"safe")
        package.writestr("unexpected.txt", b"no")
    with pytest.raises(DeliveryError, match="exactly the allowed"):
        extract_safely(archive, tmp_path / "extra-out")

    symlink = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink, "w") as package:
        for name in ALLOWED_FILES:
            info = zipfile.ZipInfo(name)
            if name == "manifest.json":
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            package.writestr(info, b"safe")
    with pytest.raises(DeliveryError, match="unsafe ZIP"):
        extract_safely(symlink, tmp_path / "symlink-out")


def test_wrong_run_and_expired_artifact_are_refused(tmp_path):
    archive = make_package(tmp_path)
    wrong = FakeClient(archive, run_record(head_branch="attacker-branch"))
    with pytest.raises(DeliveryError, match="default branch"):
        delivery(tmp_path, wrong).run(RUN_ID)

    client = FakeClient(archive)
    original = client.artifacts

    def expired(run_id):
        values = original(run_id)
        values[0]["created_at"] = "2026-07-01T00:00:00+00:00"
        return values

    client.artifacts = expired
    with pytest.raises(DeliveryError, match="freshness window"):
        delivery(tmp_path / "second", client).run(RUN_ID)


def test_count_collapse_is_a_hard_failure(tmp_path):
    current = tmp_path / "current.db"
    candidate = tmp_path / "candidate.db"
    make_timetable(current, routes=EXPECTED_FBRI | {f"extra-{n}" for n in range(30)})
    make_timetable(candidate)

    with pytest.raises(DeliveryError, match="below safe minimum") as failure:
        compare_with_current(current, validate(candidate, minimum_service_days=14))
    assert failure.value.code == "candidate_count_collapse"


def test_shadow_root_cannot_contain_or_sit_inside_live_directory(tmp_path):
    live = tmp_path / "pipeline" / "timetable.db"
    live.parent.mkdir()
    make_timetable(live)
    archive = make_package(tmp_path / "package")
    client = FakeClient(archive)

    with pytest.raises(DeliveryError, match="live timetable directory"):
        TimetableDelivery(
            DeliveryConfig(shadow_root=live.parent / "shadow", live_database=live),
            client,
        )


def test_systemd_credential_file_is_used_instead_of_process_environment(tmp_path, monkeypatch):
    credentials = tmp_path / "credentials"
    credentials.mkdir()
    token = "private-fine-grained-token-value"
    (credentials / "github-token").write_text(token + "\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credentials))
    monkeypatch.setenv("BBB_GITHUB_TOKEN", "environment-value-must-not-win")
    assert read_github_token() == token


class DownloadResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size=-1):
        if self.offset >= len(self.payload):
            return b""
        end = len(self.payload) if size < 0 else self.offset + size
        result = self.payload[self.offset:end]
        self.offset += len(result)
        return result


class DownloadOpener:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return DownloadResponse(self.payload)


def test_archive_digest_is_checked_and_bad_download_is_removed(tmp_path):
    payload = b"artifact-bytes"
    opener = DownloadOpener(payload)
    client = GitHubClient("private-token-value-long-enough", opener=opener)
    destination = tmp_path / "artifact.zip"

    with pytest.raises(DeliveryError, match="digest does not match") as failure:
        client.download(77, destination, "sha256:" + "0" * 64)
    assert failure.value.code == "archive_hash_mismatch"
    assert not destination.exists()

    expected = hashlib.sha256(payload).hexdigest()
    assert client.download(77, destination, f"sha256:{expected}") == len(payload)
    assert destination.read_bytes() == payload


def test_cross_host_artifact_redirect_drops_authorization_header():
    handler = SafeRedirectHandler()
    request = urllib.request.Request(
        "https://api.github.com/example",
        headers={"Authorization": "Bearer must-not-leak"},
    )
    redirected = handler.redirect_request(
        request, None, 302, "Found", {},
        "https://results.blob.core.windows.net/signed-artifact")
    assert redirected is not None
    assert "Authorization" not in redirected.headers
    assert "Authorization" not in redirected.unredirected_hdrs

    with pytest.raises(DeliveryError, match="untrusted artifact redirect"):
        handler.redirect_request(
            request, None, 302, "Found", {}, "https://attacker.example/archive")

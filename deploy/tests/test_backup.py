import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest


DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))

import backup
import configure_backup


def make_config(tmp_path: Path, **updates) -> backup.Config:
    mount = tmp_path / "mount"
    mount.mkdir(exist_ok=True)
    raw = {
        "staging_root": str(tmp_path / "stage"),
        "lock_file": str(tmp_path / "backup.lock"),
        "local_mountpoint": str(mount),
        "expected_mount_source": str(tmp_path / "device"),
        "local_repository": str(mount / "restic"),
        "r2_repository": "s3:https://example.r2.cloudflarestorage.com/bbb-test",
        "password_file": str(tmp_path / "local-password"),
        "r2_password_file": str(tmp_path / "r2-password"),
        "sqlite_databases": [],
        "paths": [],
        "git_repositories": [],
        "require_root": False,
        "require_mounted_local_repository": False,
        "restic_binary": "restic",
    }
    raw.update(updates)
    config_path = tmp_path / "backup.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    return backup.Config.load(config_path)


@pytest.mark.skipif(os.name == "nt", reason="example contains production POSIX paths")
def test_production_example_config_is_valid(tmp_path):
    template = json.loads(
        (DEPLOY / "backup.example.json").read_text(encoding="utf-8")
    )
    rendered = configure_backup.build_config(
        template,
        "https://darkplace.r2.cloudflarestorage.com",
        "deadbeef-dead-4bad-8dad-deadbeef0001",
        "/srv/darkplace",
    )
    config_path = tmp_path / "backup.json"
    config_path.write_text(json.dumps(rendered), encoding="utf-8")
    config = backup.Config.load(config_path)
    assert config.require_root is True
    assert config.require_mounted_local_repository is True
    assert {item.name for item in config.sqlite_databases} == {
        "collector-audit",
        "collector-live",
        "bot-app-data",
        "pipeline-timetable",
        "social",
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX wrapper is checked on Linux CI")
def test_backup_wrapper_has_valid_posix_shell_syntax():
    subprocess.run(["sh", "-n", str(DEPLOY / "run_backup.sh")], check=True)


def sqlite_source(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute("CREATE TABLE observations (id INTEGER PRIMARY KEY, value TEXT)")
    connection.commit()
    return connection


def test_online_sqlite_snapshot_includes_committed_wal_data(tmp_path: Path):
    database = tmp_path / "live.db"
    connection = sqlite_source(database)
    connection.execute("INSERT INTO observations(value) VALUES ('from-wal')")
    connection.commit()
    assert (tmp_path / "live.db-wal").exists()

    config = make_config(tmp_path, sqlite_databases=[{
        "name": "live", "path": str(database), "required": True,
    }])
    payload, manifest = backup.stage(config, integrity_check=False)
    try:
        staged = payload / "databases" / "live.db"
        with closing(sqlite3.connect(staged)) as restored:
            assert restored.execute("SELECT value FROM observations").fetchall() == [
                ("from-wal",),
            ]
        assert manifest["databases"][0]["validation"] == "quick_check"
        assert manifest["sqlite_check"] == "quick_check"
        assert any(item["path"] == "databases/live.db" for item in manifest["files"])
        assert json.loads((payload / "manifest.json").read_text())["schema"] == 1
    finally:
        connection.close()
        backup.cleanup_payload(config.staging_root, payload)


def test_full_integrity_check_is_recorded(tmp_path: Path):
    database = tmp_path / "audit.db"
    connection = sqlite_source(database)
    connection.close()
    config = make_config(tmp_path, sqlite_databases=[{
        "name": "audit", "path": str(database),
    }])
    payload, manifest = backup.stage(config, integrity_check=True)
    try:
        assert manifest["databases"][0]["validation"] == "integrity_check"
    finally:
        backup.cleanup_payload(config.staging_root, payload)


def test_corrupt_sqlite_source_fails_and_cleans_staging(tmp_path: Path):
    database = tmp_path / "broken.db"
    database.write_bytes(b"this is not sqlite")
    config = make_config(tmp_path, sqlite_databases=[{
        "name": "broken", "path": str(database),
    }])
    with pytest.raises(backup.BackupError, match="cannot snapshot SQLite"):
        backup.stage(config, integrity_check=False)
    assert not (config.staging_root / "payload").exists()


def test_git_bundle_creation_and_verification_use_repository_context(
    tmp_path: Path, monkeypatch
):
    repository = tmp_path / "published"
    repository.mkdir()
    destination = tmp_path / "published.bundle"
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    backup._git_bundle(
        backup.Source(name="published", path=repository),
        destination,
    )

    assert calls[0][0] == [
        "git", "-c", f"safe.directory={repository}", "-C", str(repository),
        "bundle", "create",
        str(destination), "--all",
    ]
    assert calls[1][0] == [
        "git", "-c", f"safe.directory={repository}", "-C", str(repository),
        "bundle", "verify",
        str(destination),
    ]


def test_restored_bundle_verification_uses_temporary_bare_repository(
    tmp_path: Path, monkeypatch
):
    bundle = tmp_path / "published.bundle"
    bundle.write_bytes(b"test")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    backup._verify_restored_git_bundle(bundle, tmp_path)

    assert calls[0][0][:3] == ["git", "init", "--bare"]
    verification = calls[1][0]
    assert verification[:2] == ["git", "-C"]
    assert verification[3:] == ["bundle", "verify", str(bundle)]


def test_sqlite_snapshot_timeout_fails_and_cleans_staging(tmp_path: Path, monkeypatch):
    database = tmp_path / "slow.db"
    connection = sqlite_source(database)
    connection.close()
    config = make_config(
        tmp_path,
        sqlite_timeout_seconds=1,
        sqlite_databases=[{"name": "slow", "path": str(database)}],
    )
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(backup.time, "monotonic", lambda: next(clock))
    with pytest.raises(backup.BackupError, match="exceeded 1s"):
        backup.stage(config, integrity_check=False)
    assert not (config.staging_root / "payload").exists()


def test_optional_missing_source_is_manifested(tmp_path: Path):
    config = make_config(tmp_path, paths=[{
        "name": "future-state", "path": str(tmp_path / "not-created"),
        "required": False,
    }])
    payload, manifest = backup.stage(config, integrity_check=False)
    try:
        assert manifest["optional_sources_absent"] == [{
            "name": "future-state", "source": str(tmp_path / "not-created"),
        }]
    finally:
        backup.cleanup_payload(config.staging_root, payload)


def test_required_missing_source_fails_and_cleans_staging(tmp_path: Path):
    config = make_config(tmp_path, paths=[{
        "name": "required", "path": str(tmp_path / "missing"),
    }])
    with pytest.raises(backup.BackupError, match="required backup source is absent"):
        backup.stage(config, integrity_check=False)
    assert not (config.staging_root / "payload").exists()


def test_config_rejects_repository_outside_mount(tmp_path: Path):
    with pytest.raises(backup.BackupError, match="below local_mountpoint"):
        make_config(tmp_path, local_repository=str(tmp_path / "elsewhere"))


def test_config_rejects_non_r2_or_credential_bearing_remote(tmp_path: Path):
    with pytest.raises(backup.BackupError, match="Cloudflare R2"):
        make_config(tmp_path, r2_repository="s3:https://example.invalid/bucket")
    with pytest.raises(backup.BackupError, match="Cloudflare R2"):
        make_config(
            tmp_path,
            r2_repository=(
                "s3:https://user:secret@example.r2.cloudflarestorage.com/bucket"),
        )


def test_config_rejects_invalid_operation_timeout(tmp_path: Path):
    with pytest.raises(backup.BackupError, match="positive integer"):
        make_config(tmp_path, restic_timeout_seconds=0)


def test_mount_guard_refuses_absent_mount(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path, require_mounted_local_repository=True)
    monkeypatch.setattr(backup, "mounted_source", lambda _mountpoint: None)
    with pytest.raises(backup.BackupError, match="refusing to use root disk"):
        backup.validate_local_mount(config)


def test_restic_minimum_version_is_enforced():
    def version(output):
        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")
        return runner

    assert backup.validate_restic_version(
        "restic", runner=version("restic 0.18.0 compiled with go1.24")) == (0, 18, 0)
    with pytest.raises(backup.BackupError, match=r"0.18.0\+"):
        backup.validate_restic_version(
            "restic", runner=version("restic 0.17.3 compiled with go1.23"))


def test_process_lock_rejects_coincident_run(tmp_path: Path):
    lock_path = tmp_path / "backup.lock"
    with backup.ProcessLock(lock_path):
        with pytest.raises(backup.AlreadyRunning):
            with backup.ProcessLock(lock_path):
                pass


def test_restic_commands_copy_before_independent_retention(tmp_path: Path, monkeypatch):
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    config = make_config(tmp_path)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "credential-id-must-not-be-an-argument")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "credential-secret-must-not-be-an-argument")
    restic = backup.Restic(config, runner=runner)
    payload = tmp_path / "payload"
    payload.mkdir()

    restic.backup(payload)
    restic.copy_to_r2()
    restic.forget_local()
    restic.forget_r2()

    actions = []
    for command, kwargs in calls:
        actions.append(next(item for item in command if item in {
            "backup", "copy", "forget",
        }))
        assert kwargs["check"] is True
        assert kwargs["timeout"] == config.restic_timeout_seconds
        assert "credential-id-must-not-be-an-argument" not in " ".join(command)
        assert "credential-secret-must-not-be-an-argument" not in " ".join(command)
    assert actions == ["backup", "copy", "forget", "forget"]
    assert calls[1][0][calls[1][0].index("-r") + 1] == config.r2_repository
    assert "--from-repo" in calls[1][0]
    assert config.local_repository in calls[1][0]
    assert list(backup.RETENTION) == calls[2][0][-7:-1]


def test_r2_init_reuses_local_chunker_parameters(tmp_path: Path):
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    config = make_config(tmp_path)
    backup.Restic(config, runner=runner).init_r2()
    command = calls[0]
    assert config.r2_repository in command
    assert ["--from-repo", config.local_repository] == command[
        command.index("--from-repo"):command.index("--from-repo") + 2
    ]
    assert "--copy-chunker-params" in command


def test_weekly_checks_read_local_data_and_rotate_r2_quarters(tmp_path: Path):
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    restic = backup.Restic(make_config(tmp_path), runner=runner)
    restic.check_local()
    restic.check_r2(subset=3)
    assert "--read-data" in calls[0]
    assert "--read-data-subset=3/4" in calls[1]
    with pytest.raises(backup.BackupError, match="between 1 and 4"):
        restic.check_r2(subset=5)


def test_restic_restore_selects_requested_repository(tmp_path: Path):
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    config = make_config(tmp_path)
    restic = backup.Restic(config, runner=runner)
    target = tmp_path / "restore"
    restic.restore("local", target)
    restic.restore("r2", target)

    assert config.local_repository in calls[0]
    assert config.r2_repository in calls[1]
    assert calls[0][-5:] == ["latest", "--tag", "bbb-backup", "--target", str(target)]


def test_restore_verifier_checks_hashes_and_sqlite_integrity(tmp_path: Path):
    database = tmp_path / "audit.db"
    connection = sqlite_source(database)
    connection.execute("INSERT INTO observations(value) VALUES ('kept')")
    connection.commit()
    connection.close()
    config = make_config(tmp_path, sqlite_databases=[{
        "name": "audit", "path": str(database),
    }])
    payload, _manifest = backup.stage(config, integrity_check=False)
    target = tmp_path / "restored"
    copied_payload = target / "var" / "tmp" / "payload"
    copied_payload.parent.mkdir(parents=True)
    shutil.copytree(payload, copied_payload)
    try:
        assert backup.verify_restore(target) == copied_payload
        (copied_payload / "unexpected.txt").write_text("not manifested", encoding="utf-8")
        with pytest.raises(backup.BackupError, match="unexpected restored path"):
            backup.verify_restore(target)
        (copied_payload / "unexpected.txt").unlink()
        (copied_payload / "databases" / "audit.db").write_bytes(b"tampered")
        with pytest.raises(backup.BackupError, match="verification failed"):
            backup.verify_restore(target)
    finally:
        backup.cleanup_payload(config.staging_root, payload)


def test_restore_target_must_be_empty_and_absolute(tmp_path: Path):
    target = tmp_path / "restore"
    target.mkdir()
    (target / "keep").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(backup.BackupError, match="must be empty"):
        backup.prepare_restore_target(target)
    with pytest.raises(backup.BackupError, match="must be an absolute"):
        backup.prepare_restore_target(Path("relative-restore"))


def test_failed_restore_removes_partial_plaintext(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    target = tmp_path / "restore"

    class BrokenRestic:
        def __init__(self, _config):
            pass

        def restore(self, _repository, restore_target):
            (restore_target / "partial-secret").write_text("partial", encoding="utf-8")
            raise backup.BackupError("simulated restore failure")

    monkeypatch.setattr(backup, "validate_runtime", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(backup, "Restic", BrokenRestic)
    with pytest.raises(backup.BackupError, match="simulated restore failure"):
        backup.run_restore(config, "r2", target)
    assert not target.exists()


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read(self, _size):
        return b"OK"


def test_healthcheck_uses_paired_start_success_and_failure_urls():
    urls = []

    def opener(request, **_kwargs):
        urls.append(request.full_url)
        return FakeResponse()

    health = backup.Healthcheck(
        "https://hc-ping.com/example-token?source=pi",
        opener=opener,
        sleeper=lambda _seconds: None,
    )
    health.ping("start")
    health.ping("success")
    health.ping("fail")

    assert urllib_path(urls[0]).endswith("/example-token/start")
    assert urllib_path(urls[1]).endswith("/example-token")
    assert urllib_path(urls[2]).endswith("/example-token/fail")
    run_ids = [urllib_query(url)["rid"] for url in urls]
    assert run_ids[0] == run_ids[1] == run_ids[2]
    assert all(urllib_query(url)["source"] == "pi" for url in urls)


def urllib_path(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).path


def urllib_query(url: str) -> dict[str, str]:
    from urllib.parse import parse_qsl, urlsplit
    return dict(parse_qsl(urlsplit(url).query))


def test_restic_failure_cleans_plaintext_staging_and_pings_fail(
    tmp_path: Path, monkeypatch,
):
    database = tmp_path / "live.db"
    connection = sqlite_source(database)
    connection.close()
    config = make_config(tmp_path, sqlite_databases=[{
        "name": "live", "path": str(database),
    }])
    states = []

    class FakeHealthcheck:
        def __init__(self, _url):
            pass

        def ping(self, state):
            states.append(state)

    class BrokenRestic:
        def __init__(self, _config):
            pass

        def backup(self, _payload):
            raise backup.BackupError("simulated restic failure")

    monkeypatch.setattr(backup, "validate_runtime", lambda _config: None)
    monkeypatch.setattr(backup, "Healthcheck", FakeHealthcheck)
    monkeypatch.setattr(backup, "Restic", BrokenRestic)

    with pytest.raises(backup.BackupError, match="simulated"):
        backup.run_backup(config, integrity_check=False)
    assert states == ["start", "fail"]
    assert not (config.staging_root / "payload").exists()

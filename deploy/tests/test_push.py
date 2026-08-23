import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import push
from local_config import settings_from
from verify_release import verify


TEST_SETTINGS = settings_from({
    "BBB_DEPLOY_USER": "rickdagless",
    "BBB_DEPLOY_HOST": "darkplace-hospital.local",
    "BBB_REMOTE_HOME": "/srv/darkplace",
    "BBB_BACKUP_UUID": "deadbeef-dead-4bad-8dad-deadbeef0001",
    "BBB_CLOUDFLARE_TUNNEL_ID": "da61e550-dead-4bad-8dad-da4cface0001",
    "BBB_LOCAL_GTFS_DIR": r"C:\Darkplace\gtfs",
})


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer syntax is checked on Linux CI")
def test_layout_installer_has_valid_posix_shell_syntax():
    subprocess.run(
        ["sh", "-n", str(push.DEPLOY / "install_unified_deploy.sh")],
        check=True,
    )


def test_dry_run_never_connects_or_reads_timetable(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(push, "Remote", lambda *_: (_ for _ in ()).throw(
        AssertionError("dry run connected")))
    missing = tmp_path / "does-not-exist.db"
    assert push.main(["--timetable", str(missing), "--dry-run"]) == 0
    assert "no build, SSH connection or live change" in capsys.readouterr().out


def test_real_deploy_refuses_a_dirty_tree_before_connecting(monkeypatch):
    monkeypatch.setattr(push, "require_clean_tree", lambda: (_ for _ in ()).throw(
        RuntimeError("working tree is not clean")))
    monkeypatch.setattr(push, "Remote", lambda *_: (_ for _ in ()).throw(
        AssertionError("dirty deploy connected")))
    assert push.main(["--component", "pipeline"]) == 1


def test_bounded_capture_does_not_wait_for_an_inherited_output_handle(tmp_path):
    release = tmp_path / "release-child"
    child = (
        "import pathlib,sys,time\n"
        "marker=pathlib.Path(sys.argv[1])\n"
        "deadline=time.monotonic()+5\n"
        "while not marker.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.02)\n"
    )
    parent = (
        "import subprocess,sys\n"
        "process=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]])\n"
        "print(process.pid, flush=True)\n"
    )
    started = time.monotonic()
    try:
        result = push.run_bounded_process(
            [sys.executable, "-c", parent, child, str(release)],
            timeout=1.5,
        )
    finally:
        release.write_text("release", encoding="utf-8")

    assert result.returncode == 0
    assert result.stdout.strip().isdigit()
    assert time.monotonic() - started < 1.25


def test_bounded_timeout_stops_only_its_spawned_process_tree(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    child = (
        "import pathlib,sys,time\n"
        "path=pathlib.Path(sys.argv[1])\n"
        "while True:\n"
        "    with path.open('a', encoding='utf-8') as stream:\n"
        "        stream.write('x')\n"
        "    time.sleep(0.02)\n"
    )
    parent = (
        "import pathlib,subprocess,sys,time\n"
        "path=pathlib.Path(sys.argv[2])\n"
        "process=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]])\n"
        "deadline=time.monotonic()+2\n"
        "while not path.exists() and time.monotonic() < deadline:\n"
        "    time.sleep(0.01)\n"
        "print(process.pid, flush=True)\n"
        "time.sleep(30)\n"
    )

    with pytest.raises(push.BoundedProcessTimeout) as caught:
        push.run_bounded_process(
            [sys.executable, "-c", parent, child, str(heartbeat)],
            timeout=0.5,
        )

    assert caught.value.stdout.strip().isdigit()
    time.sleep(0.1)
    size = heartbeat.stat().st_size
    time.sleep(0.2)
    assert heartbeat.stat().st_size == size


def test_remote_preflight_retries_a_first_login_channel_without_a_keeper(
        monkeypatch, caplog):
    calls = []

    def bounded(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            raise push.BoundedProcessTimeout(command, kwargs["timeout"], "", "")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(push, "run_bounded_process", bounded)
    remote = push.Remote(TEST_SETTINGS, preflight_timeout=0.1)

    with remote:
        pass

    assert len(calls) == 2
    assert all(call[1]["timeout"] == 0.1 for call in calls)
    assert "StrictHostKeyChecking=yes" in calls[0][0]
    assert "BatchMode=yes" in calls[0][0]
    assert "StdinNull=yes" in calls[0][0]
    assert "before any upload" in caplog.text


def test_remote_preflight_second_timeout_says_nothing_changed(monkeypatch):
    def bounded(command, **kwargs):
        raise push.BoundedProcessTimeout(command, kwargs["timeout"], "", "")

    monkeypatch.setattr(push, "run_bounded_process", bounded)
    remote = push.Remote(TEST_SETTINGS, preflight_timeout=0.1)

    with pytest.raises(RuntimeError, match="Nothing was uploaded") as caught:
        remote.__enter__()

    assert "no release was switched" in str(caught.value)
    assert "only its own SSH process trees" in str(caught.value)


def test_remote_command_timeout_is_fatal_even_when_return_codes_are_ignored(
        monkeypatch):
    def bounded(command, **kwargs):
        raise push.BoundedProcessTimeout(
            command, kwargs["timeout"], "partial output", "")

    monkeypatch.setattr(push, "run_bounded_process", bounded)
    remote = push.Remote(TEST_SETTINGS, command_timeout=0.1)

    with pytest.raises(RuntimeError, match="deployment will not continue"):
        remote.run("systemctl is-active example", check=False)


def test_remote_upload_timeout_stops_before_a_release_switch(monkeypatch, tmp_path):
    source = tmp_path / "release.tar.gz"
    source.write_bytes(b"release")

    def bounded(command, **kwargs):
        raise push.BoundedProcessTimeout(command, kwargs["timeout"], "", "")

    monkeypatch.setattr(push, "run_bounded_process", bounded)
    remote = push.Remote(TEST_SETTINGS, upload_timeout=0.1)

    with pytest.raises(RuntimeError, match="did not continue to a release switch"):
        remote.upload(source, TEST_SETTINGS.remote_base / "incoming/release.part")


def test_social_gate_installs_locked_node_dependencies_before_tests(monkeypatch):
    commands = []
    monkeypatch.setattr(push, "run_local", lambda command, **kwargs:
                        commands.append((command, kwargs.get("cwd"))))

    push.run_gates("social")

    node_commands = [command for command, cwd in commands
                     if cwd == push.REPO / "social"]
    assert node_commands == [
        [push.npm_command(), "ci", "--no-audit", "--no-fund"],
        [push.npm_command(), "test"],
    ]


@pytest.mark.parametrize("component", push.CODE_COMPONENTS)
def test_built_release_is_complete_and_contains_no_state(component, tmp_path):
    if component == "bot" and not (push.REPO / "bot/dist/index.js").exists():
        pytest.skip("bot has not been built in this checkout")
    built = push.build_release(component, tmp_path, release="test-release")
    extract = tmp_path / "extract"
    extract.mkdir()
    import tarfile
    with tarfile.open(built.archive) as archive:
        archive.extractall(extract, filter="data")
    verify(extract, component, "test-release")
    names = {path.name for path in extract.rglob("*") if path.is_file()}
    assert not names.intersection(push.FORBIDDEN_NAMES)
    if component == "bot":
        for name in (*push.BOT_ENRICHMENT_FILES, "editorial-context.json"):
            assert not (extract / name).exists()
    if component == "site":
        assert (extract / "_collector/pyproject.toml").is_file()
    if component == "collector":
        assert (extract / "check_collector_freshness.py").is_file()
        assert (extract / "compare_collectors.py").is_file()
    if component == "pipeline":
        assert (extract / "audit_integration.py").is_file()
        assert (extract / "audit_promote.py").is_file()
        assert (extract / "stop_localities.json").is_file()
        assert (extract / "audit_site_assets/index.html").is_file()
        assert (extract / "audit_site_assets/favicon.svg").is_file()
        assert (extract / "audit_site_assets/README.md").is_file()
        assert (extract / "LICENSE").is_file()
        assert (extract / "AUDIT_METHODOLOGY.md").is_file()
        assert not (extract / "fbribuses.json").exists()
    if component == "social":
        assert (extract / "social_run.py").is_file()
        assert (extract / "generate-pack.mjs").is_file()
        assert (extract / "examples/demo-pack.json").is_file()
        assert (extract / "fonts/google-sans-flex-variable.ttf").is_file()


class FakeRemote:
    def __init__(self):
        self.commands = []
        self.settings = TEST_SETTINGS

    def run(self, command, check=True):
        self.commands.append(command)
        if command.startswith("test -L"):
            return "/srv/darkplace/bbb-site\n"
        return ""

    def upload(self, source, destination, progress=False):
        self.commands.append(f"UPLOAD {destination}")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_pipeline_setup_and_health_validate_the_durable_fleet_file():
    release = TEST_SETTINGS.remote_base / "releases" / "pipeline" / "release-1"
    setup = push.setup_command("pipeline", release)
    remote = FakeRemote()

    assert f"BBB_FLEET_FILE={push.DURABLE_FLEET_FILE}" in setup
    assert push.healthy(remote, "pipeline") is True
    assert len(remote.commands) == 1
    assert f"BBB_FLEET_FILE={push.DURABLE_FLEET_FILE}" in remote.commands[0]
    assert "load_fleet_index" in remote.commands[0]


def test_failed_health_switches_back_to_previous_release(monkeypatch, tmp_path):
    archive = tmp_path / "site.tar.gz"
    archive.write_bytes(b"archive")
    built = push.BuiltRelease("site", "release-1", archive, push.sha256_file(archive))
    remote = FakeRemote()
    health = iter((False, True))
    monkeypatch.setattr(push, "wait_healthy", lambda *_: next(health))
    monkeypatch.setattr(push, "notify", lambda *_: None)

    with pytest.raises(RuntimeError, match="health gate"):
        push.deploy_release(remote, built)

    switches = [command for command in remote.commands if "mv -Tf" in command]
    assert len(switches) == 2
    assert "/releases/site/release-1" in switches[0]
    assert "/srv/darkplace/bbb-site" in switches[1]


def test_all_deploy_folds_routine_success_into_daily_update(monkeypatch, tmp_path):
    remote = FakeRemote()
    deployments = []
    messages = []
    release = "20260717t120000000000z-deadbeef"

    monkeypatch.setattr(push, "require_clean_tree", lambda: None)
    monkeypatch.setattr(push, "load_deploy_settings", lambda: TEST_SETTINGS)
    monkeypatch.setattr(push, "release_id", lambda: release)
    monkeypatch.setattr(push, "run_gates", lambda _component: None)
    monkeypatch.setattr(push, "run_local", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        push,
        "build_release",
        lambda component, _workspace, release: push.BuiltRelease(
            component, release, tmp_path / f"{component}.tar.gz", "sha256"),
    )
    monkeypatch.setattr(push, "Remote", lambda *_args: remote)
    monkeypatch.setattr(
        push,
        "deploy_release",
        lambda _remote, built, *, notify_success: deployments.append(
            (built.component, notify_success)),
    )
    monkeypatch.setattr(
        push,
        "deploy_tunnel",
        lambda _remote, _workspace, *, notify_success: deployments.append(
            ("tunnel", notify_success)),
    )
    monkeypatch.setattr(push, "notify", lambda _remote, message: messages.append(message))

    assert push.main(["--all"]) == 0
    assert deployments == [(component, False) for component in push.ALL_COMPONENTS]
    assert messages == []


def test_root_helper_and_sudoers_are_tightly_allowlisted():
    helper = (push.DEPLOY / "deploy_control.sh").read_text(encoding="utf-8")
    sudoers = (push.DEPLOY / "sudoers/bristolbusbot-deploy").read_text(encoding="utf-8")
    assert "restart:collector" in helper
    assert "restart:$component" not in helper
    assert "systemctl $" not in helper
    assert "NOPASSWD: ALL" not in sudoers
    assert "fleet-shadow:)" in helper
    assert "systemctl start bbb-fleet-shadow.service" in helper
    assert "bbb-deploy-control fleet-shadow" in sudoers
    assert "fleet-promote:)" in helper
    assert "systemctl start bbb-fleet-stage.service" in helper
    assert "fleet-auto-run:)" in helper
    assert "fleet-auto-enable:)" in helper
    assert "fleet-auto-disable:)" in helper
    assert "locality-shadow:)" in helper
    assert "locality-promote:)" in helper
    assert "locality-auto-run:)" in helper
    assert "locality-auto-enable:)" in helper
    assert "locality-auto-disable:)" in helper
    assert "bbb-deploy-control fleet-promote" in sudoers
    assert "bbb-deploy-control fleet-auto-run" in sudoers
    assert "bbb-deploy-control fleet-auto-enable" in sudoers
    assert "bbb-deploy-control fleet-auto-disable" in sudoers
    assert "bbb-deploy-control locality-shadow" in sudoers
    assert "bbb-deploy-control locality-promote" in sudoers
    assert "bbb-deploy-control locality-auto-run" in sudoers
    assert "bbb-deploy-control locality-auto-enable" in sudoers
    assert "bbb-deploy-control locality-auto-disable" in sudoers
    assert "bbb-deploy-control timetable-promote" in sudoers
    assert "timetable-auto-enable:)" in helper
    assert "timetable-auto-disable:)" in helper
    assert "bbb-deploy-control timetable-auto-enable" in sudoers
    assert "bbb-deploy-control timetable-auto-disable" in sudoers
    assert "bot-token-promote:)" in helper
    assert "@BBB_DEPLOY_BASE@/incoming/bot.env.token-new" in helper
    assert "bbb-deploy-control bot-token-promote" in sudoers
    assert "social-live-enable:)" in helper
    assert "social-live-disable:)" in helper
    assert "bbb-deploy-control social-live-enable" in sudoers
    assert "bbb-deploy-control social-live-disable" in sudoers


def test_layout_installer_waits_for_slow_startup_and_has_rollback_trap():
    installer = (push.DEPLOY / "install_unified_deploy.sh").read_text(encoding="utf-8")
    for check in ("wait_collector", "wait_site", "wait_bot", "wait_public_site"):
        assert f"{check}()" in installer
        assert f"if ! {check};" in installer
    assert "trap rollback EXIT INT TERM" in installer
    assert "previous units were restored" in installer


def test_layout_installs_shadow_validator_but_requires_credential_for_timer(tmp_path):
    installer = (push.DEPLOY / "install_unified_deploy.sh").read_text(
        encoding="utf-8")
    assert "/usr/local/libexec/bristolbusbot-timetable/timetable_delivery.py" in installer
    assert "/usr/local/libexec/bristolbusbot-timetable/timetable_promote.py" in installer
    assert "/usr/local/libexec/bristolbusbot-timetable/timetable_service_profile.py" in installer
    assert (
        '"$stage/timetable_editions.py" '
        "/usr/local/libexec/bristolbusbot-timetable/timetable_editions.py"
    ) in installer
    assert "left disabled until its root-only credential is configured" in installer

    archive = push.install_payload(tmp_path, TEST_SETTINGS)
    import tarfile
    extract = tmp_path / "shadow-layout"
    extract.mkdir()
    with tarfile.open(archive) as payload:
        payload.extractall(extract, filter="data")
    assert (extract / "timetable_delivery.py").is_file()
    assert (extract / "timetable_promote.py").is_file()
    assert (extract / "timetable_service_profile.py").is_file()
    assert (extract / "timetable_manifest.py").is_file()
    assert (extract / "timetable_editions.py").is_file()
    assert (extract / "systemd/bbb-timetable-shadow@.service").is_file()
    assert (extract / "systemd/bbb-timetable-shadow-auto.service").is_file()
    assert (extract / "systemd/bbb-timetable-promote@.service").is_file()
    assert (extract / "editorial_context.py").is_file()
    assert (extract / "editorial_fetch.py").is_file()
    assert (extract / "editorial_promote.py").is_file()
    assert (extract / "editorial-context.json").is_file()
    assert (extract / "systemd/bbb-editorial-fetch.service").is_file()
    assert (extract / "systemd/bbb-editorial-promote.service").is_file()
    assert (extract / "systemd/bbb-editorial-refresh.timer").is_file()
    assert "enable --now bbb-editorial-refresh.timer" in installer
    assert "enable --now bbb-data-health.timer" in installer
    assert (extract / "configure_social_curation.py").is_file()
    assert (extract / "configure_blurb_generation.py").is_file()
    assert (extract / "blurb_automation.py").is_file()
    assert (extract / "blurb_review.sh").is_file()
    assert (extract / "model-context.json").is_file()
    assert (extract / "enrichment_layout.py").is_file()
    assert (extract / "data_promotion.py").is_file()
    assert (extract / "enrichment_contracts.py").is_file()
    assert (extract / "enrichment_promote.py").is_file()
    assert (extract / "update_fleet_data.py").is_file()
    assert (extract / "fleet_candidate_stage.py").is_file()
    assert (extract / "geocode_stops.py").is_file()
    assert (extract / "locality_candidate_stage.py").is_file()
    assert (extract / "systemd/bbb-enrichment-promote@.service").is_file()
    assert not (extract / "systemd/bbb-enrichment-promote.timer").exists()
    assert (extract / "systemd/bbb-fleet-shadow.service").is_file()
    assert not (extract / "systemd/bbb-fleet-shadow.timer").exists()
    assert (extract / "systemd/bbb-fleet-refresh.service").is_file()
    assert (extract / "systemd/bbb-fleet-stage.service").is_file()
    assert (extract / "systemd/bbb-fleet-refresh.timer").is_file()
    assert (extract / "systemd/bbb-locality-refresh.service").is_file()
    assert (extract / "systemd/bbb-locality-shadow.service").is_file()
    assert (extract / "systemd/bbb-locality-stage.service").is_file()
    assert "Fleet refresh timer installed but left disabled" in installer
    assert (extract / "data_health.py").is_file()
    assert (extract / "systemd/bbb-data-health.service").is_file()
    assert (extract / "systemd/bbb-data-health.timer").is_file()
    assert (extract / "collector_anomaly_report.py").is_file()
    assert (extract / "audit_origin_backfill.py").is_file()
    assert (extract / "systemd/bbb-collector-anomaly.service").is_file()
    assert (extract / "systemd/bbb-collector-anomaly.timer").is_file()
    assert "enable --now bbb-collector-anomaly.timer" in installer
    assert (extract / "systemd/bbb-blurb-generate.service").is_file()
    assert (extract / "systemd/bbb-blurb-generate.timer").is_file()
    assert (extract / "systemd/bbb-blurb-promote.service").is_file()
    assert "Blurb generation timer installed but left disabled" in installer
    assert (extract / "systemd/bbb-social-curation.service").is_file()
    assert (extract / "systemd/bbb-social-curation.timer").is_file()
    assert "Social curation timer installed but left disabled" in installer
    assert "/usr/local/libexec/bbb-data-health" in installer
    assert "/usr/local/libexec/bbb-collector-anomaly" in installer
    assert "/usr/local/libexec/bbb-audit-origin-backfill" in installer
    assert "/usr/local/libexec/bristolbusbot-enrichment/" \
        "enrichment_promote.py" in installer
    assert "/usr/local/libexec/bristolbusbot-enrichment/" \
        "update_fleet_data.py" in installer
    assert "/usr/local/libexec/bristolbusbot-enrichment/" \
        "fleet_candidate_stage.py" in installer
    assert "/usr/local/libexec/bristolbusbot-enrichment/" \
        "geocode_stops.py" in installer
    assert "/usr/local/libexec/bristolbusbot-enrichment/" \
        "locality_candidate_stage.py" in installer
    assert '"$enrichment_dir/incoming"' in installer


def test_layout_migrates_and_health_gates_durable_bot_enrichment():
    installer = (push.DEPLOY / "install_unified_deploy.sh").read_text(
        encoding="utf-8")
    assert installer.count("bbb-enrichment-layout migrate") == 1
    assert 'bot_migration_source=$(resolve_migration_source' in installer
    assert 'site_migration_source=$(resolve_migration_source' in installer
    assert '"$releases/$component/"*|"$legacy"' in installer
    assert '--source "$bot_migration_source"' in installer
    assert '--source "$site_migration_source"' in installer
    assert '--source "$stage"' in installer
    assert '--destination "$enrichment_dir"' in installer
    assert "bbb-enrichment-layout validate --quiet" in installer
    for name in push.BOT_ENRICHMENT_FILES:
        assert f'"$enrichment_dir/{name}"' in installer
    for name in (
        "bus-descriptions.json", "waiting-descriptions.json",
        "depot-descriptions.json", "model-context.json",
    ):
        assert f'"$enrichment_dir/{name}"' in installer
    assert "bbb-configure-blurb-generation" in installer
    assert "/usr/local/bin/bbb-blurb-review" in installer
    assert "/etc/bristolbusbot/backup.json" in installer


def test_bot_health_requires_durable_data_databases_and_fleet():
    remote = FakeRemote()

    assert push.healthy(remote, "bot") is True
    command = remote.commands[0]
    assert "bbb-enrichment-layout validate --quiet" in command
    assert '["timetable"]["connected"] is True' in command
    assert '["appData"]["connected"] is True' in command
    assert '["fleet"]["loaded"] is True' in command
    assert '["fleet"]["records"] ==' in command
    assert '["localities"]["loaded"] is True' in command
    assert '["localities"]["records"] > 0' in command


def test_layout_update_preserves_existing_current_release_links():
    installer = (push.DEPLOY / "install_unified_deploy.sh").read_text(
        encoding="utf-8"
    )
    assert 'if [ ! -e "$link" ] && [ ! -L "$link" ]; then' in installer
    assert 'ln -s "$legacy" "$link"' in installer
    assert "ln -sfn" not in installer


def test_layout_rollback_restores_helpers_and_removes_new_units():
    installer = (push.DEPLOY / "install_unified_deploy.sh").read_text(
        encoding="utf-8")
    assert 'printf \'%s %s\\n\' "$name" "$destination" >> "$backup/file-map"' in installer
    assert 'cp -p "$backup/files/$name" "$destination" || true' in installer
    assert 'rm -f "/etc/systemd/system/$name"' in installer
    assert 'rm -f "$destination"' in installer
    assert installer.index("changed=1") < installer.index(
        'install -o root -g root -m 0755 "$stage/deploy_control.sh"')


def test_layout_payload_renders_all_private_identity_tokens(tmp_path):
    archive = push.install_payload(tmp_path, TEST_SETTINGS)
    import tarfile
    extract = tmp_path / "layout"
    extract.mkdir()
    with tarfile.open(archive) as payload:
        payload.extractall(extract, filter="data")
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in extract.rglob("*") if path.is_file()
    )
    assert "@BBB_" not in combined
    assert "User=rickdagless" in combined
    assert "/srv/darkplace/bristolbusbot/current/site" in combined

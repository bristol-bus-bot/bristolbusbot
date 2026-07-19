import sys
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


@pytest.mark.parametrize("component", push.CODE_COMPONENTS)
def test_built_release_is_complete_and_contains_no_state(component, tmp_path):
    if component == "bot" and not (push.REPO / "bot/dist/index.js").exists():
        pytest.skip("bot has not been built in this checkout")
    if component == "bot" and not (push.REPO / "bot/data/fbribuses.json").exists():
        pytest.skip("ignored runtime fleet cache is not present in this checkout")
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
        assert (extract / "stop_enrichment.json").is_file()
        assert (extract / "route_details.json").is_file()
    if component == "site":
        assert (extract / "_collector/pyproject.toml").is_file()
    if component == "pipeline":
        assert (extract / "audit_integration.py").is_file()
        assert (extract / "audit_promote.py").is_file()
        assert (extract / "audit_site_assets/index.html").is_file()
        assert (extract / "audit_site_assets/README.md").is_file()
        assert (extract / "LICENSE").is_file()
        assert (extract / "AUDIT_METHODOLOGY.md").is_file()


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


def test_all_deploy_sends_one_summary_success_alert(monkeypatch, tmp_path):
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
    assert messages == [
        ":white_check_mark: BristolBusBot full deployment complete "
        f"{release} ({', '.join(push.ALL_COMPONENTS)})"
    ]


def test_root_helper_and_sudoers_are_tightly_allowlisted():
    helper = (push.DEPLOY / "deploy_control.sh").read_text(encoding="utf-8")
    sudoers = (push.DEPLOY / "sudoers/bristolbusbot-deploy").read_text(encoding="utf-8")
    assert "restart:collector" in helper
    assert "restart:$component" not in helper
    assert "systemctl $" not in helper
    assert "NOPASSWD: ALL" not in sudoers
    assert "bbb-deploy-control timetable-promote" in sudoers
    assert "bot-token-promote:)" in helper
    assert "@BBB_DEPLOY_BASE@/incoming/bot.env.token-new" in helper
    assert "bbb-deploy-control bot-token-promote" in sudoers


def test_layout_installer_waits_for_slow_startup_and_has_rollback_trap():
    installer = (push.DEPLOY / "install_unified_deploy.sh").read_text(encoding="utf-8")
    for check in ("wait_collector", "wait_site", "wait_bot", "wait_public_site"):
        assert f"{check}()" in installer
        assert f"if ! {check};" in installer
    assert "trap rollback EXIT INT TERM" in installer
    assert "previous units were restored" in installer


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

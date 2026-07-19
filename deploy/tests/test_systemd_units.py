from pathlib import Path


SYSTEMD = Path(__file__).resolve().parents[1] / "systemd"


def test_site_unit_has_required_lifecycle_and_accounting():
    source = (SYSTEMD / "bbb-site.service").read_text(encoding="utf-8")
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "Restart=always",
        "RestartSec=5s",
        "WantedBy=multi-user.target",
        "CPUAccounting=yes",
        "MemoryAccounting=yes",
        "TasksAccounting=yes",
    ):
        assert setting in source


def test_site_unit_is_read_only_and_sandboxed():
    source = (SYSTEMD / "bbb-site.service").read_text(encoding="utf-8")
    for setting in (
        "NoNewPrivileges=yes",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
        "MemoryDenyWriteExecute=yes",
        "PrivateTmp=yes",
        "ProcSubset=pid",
        "ProtectHome=read-only",
        "ProtectSystem=strict",
        "CapabilityBoundingSet=",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    ):
        assert setting in source
    assert "ReadWritePaths=" not in source
    assert "--no-control-socket" in source


def test_site_uses_atomic_current_release_path():
    source = (SYSTEMD / "bbb-site.service").read_text(encoding="utf-8")
    assert "@BBB_DEPLOY_BASE@/current/site" in source
    assert "/home/" not in source


def test_collector_unit_has_exact_writable_state_and_network_access():
    source = (SYSTEMD / "bbb-collector.service").read_text(encoding="utf-8")
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "Restart=always",
        "RestartSec=5s",
        "WantedBy=multi-user.target",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "ReadWritePaths=/var/lib/bristolbusbot/collector",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "MemoryAccounting=yes",
    ):
        assert setting in source
    assert "IPAddressDeny=any" not in source


def test_bot_unit_allows_only_its_two_writable_databases():
    source = (SYSTEMD / "bbb-bot.service").read_text(encoding="utf-8")
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "Restart=always",
        "ProtectSystem=strict",
        "ProtectHome=read-only",
        "ReadWritePaths=/var/lib/bristolbusbot/bot /var/lib/bristolbusbot/collector",
        "Environment=RUNTIME_MANAGER=systemd",
        "Environment=ENABLE_FILE_LOGS=false",
        "Environment=RARE_WORKING_SHADOW=true",
        "Environment=AUDIT_INTEGRATION_PATH=/var/lib/bristolbusbot/pipeline/audit_site/audit_integration.json",
        "MemoryAccounting=yes",
    ):
        assert setting in source
    # V8's JIT requires writable executable mappings.
    assert "MemoryDenyWriteExecute=yes" not in source


def test_tunnel_unit_is_fully_read_only_and_has_no_home_access():
    source = (SYSTEMD / "bbb-tunnel.service").read_text(encoding="utf-8")
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "Restart=always",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "MemoryDenyWriteExecute=yes",
        "--no-autoupdate",
        "/etc/bristolbusbot/cloudflared/config.yml",
    ):
        assert setting in source
    assert "ReadWritePaths=" not in source


def test_every_calendar_timer_is_persistent_and_installable():
    timers = list(SYSTEMD.glob("bbb-*.timer"))
    assert len(timers) >= 8
    for path in timers:
        source = path.read_text(encoding="utf-8")
        assert "WantedBy=timers.target" in source, path.name
        assert "Unit=bbb-" in source, path.name
        if "OnCalendar=" in source:
            assert "Persistent=true" in source, path.name


def test_every_timer_job_has_baseline_sandboxing():
    jobs = [path for path in SYSTEMD.glob("bbb-*.service")
            if path.name not in {
                "bbb-site.service", "bbb-collector.service",
                "bbb-bot.service", "bbb-tunnel.service",
            }]
    assert len(jobs) >= 8
    for path in jobs:
        source = path.read_text(encoding="utf-8")
        assert "NoNewPrivileges=yes" in source, path.name
        assert "PrivateTmp=yes" in source, path.name
        assert "ProtectSystem=strict" in source, path.name
        assert "TimeoutStartSec=" in source, path.name


def test_rollup_is_networkless_and_publish_does_not_repeat_it():
    rollup = (SYSTEMD / "bbb-audit-rollup.service").read_text(encoding="utf-8")
    publish = (SYSTEMD.parent / "publish_to_github.sh").read_text(encoding="utf-8")
    assert "IPAddressDeny=any" in rollup
    assert "audit_rollup.py" not in publish
    assert 'install -m 0644 "$AUDIT_DIR/LICENSE" LICENSE' in publish
    assert 'install -m 0644 "$ASSET_DIR/README.md" README.md' in publish
    assert 'install -m 0644 "$AUDIT_DIR/AUDIT_METHODOLOGY.md" AUDIT_METHODOLOGY.md' in publish
    assert "git add LICENSE README.md AUDIT_METHODOLOGY.md" in publish


def test_integration_is_built_networkless_and_promoted_only_after_publish():
    runner = (SYSTEMD.parent / "run_audit_rollup.sh").read_text(encoding="utf-8")
    publish = (SYSTEMD.parent / "publish_to_github.sh").read_text(encoding="utf-8")
    assert "audit_integration.py" in runner
    assert "audit_integration.pending.json" in runner
    assert "audit_promote.py" in publish
    assert publish.index("git push origin main") < publish.index("audit_promote.py")
    assert publish.index("audit_promote.py") < publish.index('notify ":white_check_mark:')


def test_backup_sandbox_cache_directory_is_created_before_unit_start():
    source = (SYSTEMD.parent / "tmpfiles" / "bristolbusbot.conf").read_text(
        encoding="utf-8")
    assert "d /var/cache/bristolbusbot 0700 root root -" in source
    assert "/var/tmp/bristolbusbot-backup" not in source

    for name in ("bbb-backup.service", "bbb-backup-check.service"):
        unit = (SYSTEMD / name).read_text(encoding="utf-8")
        assert "ReadWritePaths=/mnt/bbb-backup /var/cache/bristolbusbot" in unit
        assert "/var/tmp/bristolbusbot-backup" not in unit
        assert "PrivateDevices=no" in unit
        assert "DevicePolicy=closed" in unit

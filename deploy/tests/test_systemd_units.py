from pathlib import Path


SYSTEMD = Path(__file__).resolve().parents[1] / "systemd"


def test_site_unit_has_required_lifecycle_and_accounting():
    source = (SYSTEMD / "bbb-site.service").read_text(encoding="utf-8")
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "Restart=always",
        "RestartSec=5s",
        "WantedBy=multi-user.target",
        "Environment=BBB_FLEET_JSON=/var/lib/bristolbusbot/enrichment/"
        "fbribuses.json",
        "Environment=BBB_LOCALITIES_JSON=/var/lib/bristolbusbot/enrichment/"
        "stop_localities.json",
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
        "Environment=EDITORIAL_CONTEXT_PATH=/var/lib/bristolbusbot-editorial/editorial-context.json",
        "Environment=EDITORIAL_USAGE_PATH=/var/lib/bristolbusbot/bot/editorial-usage.json",
        "Environment=BBB_FLEET_JSON=/var/lib/bristolbusbot/enrichment/fbribuses.json",
        "Environment=BBB_LOCALITIES_JSON=/var/lib/bristolbusbot/enrichment/stop_localities.json",
        "Environment=BBB_ENRICHMENT_JSON=/var/lib/bristolbusbot/enrichment/stop_enrichment.json",
        "Environment=BBB_LOCAL_FLAVOUR_JSON=/var/lib/bristolbusbot/enrichment/local_flavour.json",
        "Environment=BBB_ROUTE_DETAILS_JSON=/var/lib/bristolbusbot/enrichment/route_details.json",
        "ReadOnlyPaths=/var/lib/bristolbusbot/enrichment",
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
        "RestartSec=30s",
        "StartLimitIntervalSec=0",
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "MemoryDenyWriteExecute=yes",
        "--no-autoupdate",
        "/etc/bristolbusbot/cloudflared/config.yml",
    ):
        assert setting in source
    assert "StartLimitBurst=" not in source
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


def test_data_health_job_is_nightly_networkless_and_report_only():
    service = (SYSTEMD / "bbb-data-health.service").read_text(encoding="utf-8")
    timer = (SYSTEMD / "bbb-data-health.timer").read_text(encoding="utf-8")
    for setting in (
        "--name data-health",
        "/usr/local/libexec/bbb-data-health",
        "ReadWritePaths=/var/lib/bristolbusbot/monitoring",
        "ProtectSystem=strict",
        "IPAddressDeny=any",
        "RestrictAddressFamilies=AF_UNIX",
    ):
        assert setting in service
    assert "OnCalendar=*-*-* 04:15:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=bbb-data-health.service" in timer


def test_collector_anomaly_job_is_networkless_bounded_and_report_only():
    service = (SYSTEMD / "bbb-collector-anomaly.service").read_text(
        encoding="utf-8")
    timer = (SYSTEMD / "bbb-collector-anomaly.timer").read_text(
        encoding="utf-8")
    for setting in (
        "--name collector-anomaly",
        "/usr/local/libexec/bbb-collector-anomaly",
        "--window-hours 48",
        "ReadOnlyPaths=/var/lib/bristolbusbot/collector/audit.db "
        "/var/lib/bristolbusbot/pipeline/timetable.db",
        "ReadWritePaths=/var/lib/bristolbusbot/monitoring",
        "ProtectSystem=strict",
        "IPAddressDeny=any",
        "RestrictAddressFamilies=AF_UNIX",
    ):
        assert setting in service
    assert "OnCalendar=*-*-* 04:40:00" in timer
    assert "Persistent=true" in timer
    assert "Unit=bbb-collector-anomaly.service" in timer


def test_social_curation_is_credentialed_isolated_and_shadow_gated():
    service = (SYSTEMD / "bbb-social-curation.service").read_text(
        encoding="utf-8")
    timer = (SYSTEMD / "bbb-social-curation.timer").read_text(
        encoding="utf-8")
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "EnvironmentFile=/etc/bristolbusbot/social.env",
        "LoadCredential=slack-token:/etc/bristolbusbot/social-slack.token",
        "ExecStartPre=+/usr/local/libexec/bbb-validate-config social",
        "--name social-curation",
        "current/social/social_run.py",
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "ProtectHome=read-only",
        "ProtectSystem=strict",
        "ReadOnlyPaths=/var/lib/bristolbusbot/bot/app_data.db "
        "/var/lib/bristolbusbot/collector/audit.db",
        "ReadWritePaths=/var/lib/bristolbusbot/social "
        "/var/lib/bristolbusbot/monitoring",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    ):
        assert setting in service
    assert "MemoryDenyWriteExecute=yes" not in service
    assert "OnCalendar=*:0/3" in timer
    assert "Persistent=true" in timer
    assert "Unit=bbb-social-curation.service" in timer

    tmpfiles = (SYSTEMD.parent / "tmpfiles" / "bristolbusbot.conf").read_text(
        encoding="utf-8")
    assert (
        "d /var/lib/bristolbusbot/social 0750 "
        "@BBB_DEPLOY_USER@ @BBB_DEPLOY_USER@ -"
    ) in tmpfiles
    assert (
        "f /var/lib/bristolbusbot/social/social.db 0600 "
        "@BBB_DEPLOY_USER@ @BBB_DEPLOY_USER@ -"
    ) in tmpfiles
    assert "/var/lib/bristolbusbot/social.db" not in service


def test_layout_migrates_social_sqlite_into_its_writable_directory():
    installer = (SYSTEMD.parent / "install_unified_deploy.sh").read_text(
        encoding="utf-8")
    assert "social_legacy_db=/var/lib/bristolbusbot/social.db" in installer
    assert "social_db=$social_state_dir/social.db" in installer
    assert 'mv "$social_legacy_db" "$social_db"' in installer
    assert "BBB_SOCIAL_DB=/var/lib/bristolbusbot/social/social.db" in installer
    assert 'validate_production_config.py" social' in installer
    assert 'if [ "$social_db_migrated" -eq 1 ]' in installer
    assert installer.index("systemctl stop bbb-social-curation.timer") \
        < installer.index('mv "$social_legacy_db" "$social_db"')


def test_rollup_is_networkless_and_publish_does_not_repeat_it():
    rollup = (SYSTEMD / "bbb-audit-rollup.service").read_text(encoding="utf-8")
    runner = (SYSTEMD.parent / "run_audit_rollup.sh").read_text(encoding="utf-8")
    tmpfiles = (SYSTEMD.parent / "tmpfiles" / "bristolbusbot.conf").read_text(
        encoding="utf-8")
    publish = (SYSTEMD.parent / "publish_to_github.sh").read_text(encoding="utf-8")
    assert "IPAddressDeny=any" in rollup
    assert (
        "Environment=BBB_FLEET_FILE=/var/lib/bristolbusbot/enrichment/"
        "fbribuses.json"
    ) in rollup
    assert (
        "ReadOnlyPaths=/var/lib/bristolbusbot/enrichment/fbribuses.json"
    ) in rollup
    assert (
        "BBB_FLEET_FILE=/var/lib/bristolbusbot/enrichment/fbribuses.json"
    ) in runner
    assert (
        "d /var/lib/bristolbusbot/enrichment 0750 "
        "@BBB_DEPLOY_USER@ @BBB_DEPLOY_USER@ -"
    ) in tmpfiles
    assert "audit_rollup.py" not in publish
    assert 'install -m 0644 "$AUDIT_DIR/LICENSE" LICENSE' in publish
    assert 'install -m 0644 "$ASSET_DIR/README.md" README.md' in publish
    assert 'install -m 0644 "$ASSET_DIR/favicon.svg" docs/favicon.svg' in publish
    assert 'install -m 0644 "$AUDIT_DIR/AUDIT_METHODOLOGY.md" AUDIT_METHODOLOGY.md' in publish
    assert "git add LICENSE README.md AUDIT_METHODOLOGY.md" in publish


def test_audit_rollup_waits_until_the_previous_service_day_is_closed():
    rollup = (SYSTEMD / "bbb-audit-rollup.timer").read_text(encoding="utf-8")
    publish = (SYSTEMD / "bbb-audit-publish.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 05:15:00" in rollup
    assert "OnCalendar=*-*-* 05:45:00" in publish


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


def test_timetable_auto_and_attended_shadows_have_separate_promotion_topology():
    attended = (SYSTEMD / "bbb-timetable-shadow@.service").read_text(
        encoding="utf-8")
    automatic = (SYSTEMD / "bbb-timetable-shadow-auto.service").read_text(
        encoding="utf-8")
    timer = (SYSTEMD / "bbb-timetable-shadow.timer").read_text(encoding="utf-8")
    for service in (attended, automatic):
        assert service.startswith("[Unit]\n")
        for setting in (
            "User=@BBB_DEPLOY_USER@",
            "EnvironmentFile=-/etc/bristolbusbot/timetable-delivery.env",
            "LoadCredential=github-token:/etc/bristolbusbot/timetable-delivery.token",
            "NoNewPrivileges=yes",
            "ProtectHome=yes",
            "ProtectSystem=strict",
            "ProtectProc=invisible",
            "ProcSubset=pid",
            "ReadWritePaths=/var/lib/bristolbusbot/timetable-shadow "
            "/var/lib/bristolbusbot/monitoring /run/lock/bristolbusbot",
            "/run/lock/bristolbusbot/heavy-io.lock",
            "flock -w 900 -E 73",
        ):
            assert setting in service
        assert ".timetable.db.upload" not in service
    assert "OnSuccess=bbb-timetable-promote@auto.service" not in attended
    assert "--run-id %i" in attended
    assert "OnSuccess=bbb-timetable-promote@auto.service" in automatic
    assert "--run-id auto" in automatic
    assert "Unit=bbb-timetable-shadow-auto.service" in timer
    assert "05:00:00" in timer


def test_timetable_promoter_is_root_fixed_path_and_sandboxed():
    service = (SYSTEMD / "bbb-timetable-promote@.service").read_text(
        encoding="utf-8")
    for setting in (
        "User=root",
        "--name timetable-promote --skip-exit-code 75",
        "/usr/local/libexec/bristolbusbot-timetable/timetable_promote.py --candidate %i",
        "/run/lock/bristolbusbot/heavy-io.lock",
        "NoNewPrivileges=yes",
        "ProtectHome=yes",
        "ProtectSystem=strict",
        "ReadWritePaths=/var/lib/bristolbusbot/pipeline "
        "/var/lib/bristolbusbot/monitoring /run/lock/bristolbusbot",
        "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER",
        "TimeoutStartSec=20min",
    ):
        assert setting in service
    assert "/var/lib/bristolbusbot/timetable-shadow" not in next(
        line for line in service.splitlines() if line.startswith("ReadWritePaths="))
    assert "OnSuccess=bbb-locality-refresh.service" in service


def test_enrichment_promoter_is_fixed_dormant_and_uses_shared_lock():
    service = (SYSTEMD / "bbb-enrichment-promote@.service").read_text(
        encoding="utf-8")
    for setting in (
        "User=root",
        "--name enrichment-promote-%i --skip-exit-code 75",
        "/run/lock/bristolbusbot/heavy-io.lock",
        "flock -w 900 -E 73",
        "/usr/local/libexec/bristolbusbot-enrichment/"
        "enrichment_promote.py %i",
        "ProtectSystem=strict",
        "ReadWritePaths=/var/lib/bristolbusbot/enrichment "
        "/var/lib/bristolbusbot/monitoring /run/lock/bristolbusbot",
        "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
        "TimeoutStartSec=10min",
    ):
        assert setting in service
    assert not (SYSTEMD / "bbb-enrichment-promote.timer").exists()


def test_fleet_shadow_is_isolated_networked_and_never_scheduled():
    service = (SYSTEMD / "bbb-fleet-shadow.service").read_text(
        encoding="utf-8")
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "--name fleet-shadow",
        "flock -w 900 -E 73",
        "/usr/local/libexec/bristolbusbot-enrichment/update_fleet_data.py",
        "--live /var/lib/bristolbusbot/enrichment/fbribuses.json",
        "--candidate /var/lib/bristolbusbot/fleet-shadow/fbribuses.json",
        "--report /var/lib/bristolbusbot/monitoring/fleet-shadow.json",
        "ReadOnlyPaths=/var/lib/bristolbusbot/enrichment/fbribuses.json",
        "ReadWritePaths=/var/lib/bristolbusbot/fleet-shadow "
        "/var/lib/bristolbusbot/monitoring /run/lock/bristolbusbot",
        "ProtectSystem=strict",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    ):
        assert setting in service
    assert "OnSuccess=" not in service
    assert not (SYSTEMD / "bbb-fleet-shadow.timer").exists()


def test_fleet_refresh_chains_validation_staging_and_guarded_promotion():
    refresh = (SYSTEMD / "bbb-fleet-refresh.service").read_text(
        encoding="utf-8")
    stage = (SYSTEMD / "bbb-fleet-stage.service").read_text(encoding="utf-8")
    timer = (SYSTEMD / "bbb-fleet-refresh.timer").read_text(encoding="utf-8")
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "OnSuccess=bbb-fleet-stage.service",
        "--name fleet-refresh",
        "update_fleet_data.py",
        "/run/lock/bristolbusbot/heavy-io.lock",
        "ReadOnlyPaths=/var/lib/bristolbusbot/enrichment/fbribuses.json",
        "ProtectSystem=strict",
    ):
        assert setting in refresh
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "OnSuccess=bbb-enrichment-promote@fleet.service",
        "--name fleet-stage",
        "fleet_candidate_stage.py",
        "/run/lock/bristolbusbot/heavy-io.lock",
        "ReadOnlyPaths=/var/lib/bristolbusbot/fleet-shadow/fbribuses.json "
        "/var/lib/bristolbusbot/enrichment/fbribuses.json",
        "ReadWritePaths=/var/lib/bristolbusbot/enrichment/incoming "
        "/var/lib/bristolbusbot/monitoring /run/lock/bristolbusbot",
        "IPAddressDeny=any",
    ):
        assert setting in stage
    assert "OnCalendar=Mon *-*-* 00:45:00" in timer
    assert "Persistent=true" in timer
    assert "RandomizedDelaySec=5min" in timer


def test_locality_refresh_is_timetable_triggered_exact_and_independently_promoted():
    refresh = (SYSTEMD / "bbb-locality-refresh.service").read_text(
        encoding="utf-8")
    shadow = (SYSTEMD / "bbb-locality-shadow.service").read_text(
        encoding="utf-8")
    stage = (SYSTEMD / "bbb-locality-stage.service").read_text(
        encoding="utf-8")
    for setting in (
        "OnSuccess=bbb-locality-stage.service",
        "ConditionPathExists=/etc/bristolbusbot/locality-refresh-enabled",
        "--name locality-refresh",
        "geocode_stops.py",
        "--timetable /var/lib/bristolbusbot/pipeline/timetable.db",
        "--live /var/lib/bristolbusbot/enrichment/stop_localities.json",
        "--candidate /var/lib/bristolbusbot/locality-shadow/stop_localities.json",
        "--report /var/lib/bristolbusbot/monitoring/locality-shadow.json",
        "ReadOnlyPaths=/var/lib/bristolbusbot/pipeline/timetable.db "
        "/var/lib/bristolbusbot/enrichment/stop_localities.json",
    ):
        assert setting in refresh
    assert "OnSuccess=" not in shadow
    assert "--force-boundary-refresh" in shadow
    for setting in (
        "OnSuccess=bbb-enrichment-promote@localities.service",
        "--name locality-stage",
        "locality_candidate_stage.py",
        "IPAddressDeny=any",
    ):
        assert setting in stage
    assert not (SYSTEMD / "bbb-locality-refresh.timer").exists()
    tmpfiles = (SYSTEMD.parent / "tmpfiles" / "bristolbusbot.conf").read_text(
        encoding="utf-8")
    assert (
        "d /var/lib/bristolbusbot/locality-shadow 0750 "
        "@BBB_DEPLOY_USER@ @BBB_DEPLOY_USER@ -"
    ) in tmpfiles


def test_blurb_generation_is_bounded_pending_only_and_human_promoted():
    generation = (SYSTEMD / "bbb-blurb-generate.service").read_text(
        encoding="utf-8")
    timer = (SYSTEMD / "bbb-blurb-generate.timer").read_text(
        encoding="utf-8")
    promotion = (SYSTEMD / "bbb-blurb-promote.service").read_text(
        encoding="utf-8")
    for setting in (
        "User=@BBB_DEPLOY_USER@",
        "ConditionPathExists=/etc/bristolbusbot/blurb-generation-enabled",
        "EnvironmentFile=-/etc/bristolbusbot/blurb.env",
        "--name blurb-generate --skip-exit-code 75",
        "blurb_automation.py generate",
        "ReadOnlyPaths=/var/lib/bristolbusbot/enrichment",
        "ReadWritePaths=/var/lib/bristolbusbot/blurb-pending "
        "/var/lib/bristolbusbot/monitoring /run/lock/bristolbusbot",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
    ):
        assert setting in generation
    assert "OnCalendar=Mon *-*-* 02:15:00" in timer
    assert "OnSuccess=" not in generation
    for setting in (
        "User=root",
        "--name blurb-promote",
        "blurb_automation.py promote",
        "ReadWritePaths=/var/lib/bristolbusbot/enrichment "
        "/var/lib/bristolbusbot/blurb-pending "
        "/var/lib/bristolbusbot/monitoring /run/lock/bristolbusbot",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
        "CapabilityBoundingSet=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER",
    ):
        assert setting in promotion
    assert not (SYSTEMD / "bbb-blurb-promote.timer").exists()


def test_blurb_enable_revalidates_or_seeds_configuration_first():
    control = (SYSTEMD.parent / "deploy_control.sh").read_text(encoding="utf-8")
    section = control.split("blurb-auto-enable:)", 1)[1].split(
        "blurb-auto-disable:)", 1)[0]

    configure = section.index("bbb-configure-blurb-generation")
    require_env = section.index("test -f /etc/bristolbusbot/blurb.env")
    enable_timer = section.index("systemctl enable --now bbb-blurb-generate.timer")
    assert configure < require_env < enable_timer


def test_heavy_io_jobs_share_one_lock_with_backup_precedence():
    for name in (
        "bbb-backup.service",
        "bbb-backup-check.service",
        "bbb-audit-rollup.service",
        "bbb-timetable-shadow-auto.service",
        "bbb-timetable-shadow@.service",
        "bbb-timetable-promote@.service",
        "bbb-enrichment-promote@.service",
        "bbb-fleet-shadow.service",
        "bbb-fleet-refresh.service",
        "bbb-fleet-stage.service",
        "bbb-locality-shadow.service",
        "bbb-locality-refresh.service",
        "bbb-locality-stage.service",
        "bbb-blurb-generate.service",
        "bbb-blurb-promote.service",
    ):
        source = (SYSTEMD / name).read_text(encoding="utf-8")
        assert "/run/lock/bristolbusbot/heavy-io.lock" in source
    backup = (SYSTEMD / "bbb-backup.service").read_text(encoding="utf-8")
    delivery = (SYSTEMD / "bbb-timetable-shadow@.service").read_text(encoding="utf-8")
    assert "flock -n -E 75" in backup
    assert "flock -w 900 -E 73" in delivery
    tmpfiles = (SYSTEMD.parent / "tmpfiles" / "bristolbusbot.conf").read_text(
        encoding="utf-8")
    assert (
        "f /run/lock/bristolbusbot/heavy-io.lock "
        "0660 @BBB_DEPLOY_USER@ @BBB_DEPLOY_USER@ -"
    ) in tmpfiles
    assert (
        "z /run/lock/bristolbusbot/heavy-io.lock "
        "0660 @BBB_DEPLOY_USER@ @BBB_DEPLOY_USER@ -"
    ) in tmpfiles
    installer = (SYSTEMD.parent / "install_unified_deploy.sh").read_text(
        encoding="utf-8")
    assert 'if [ -L "$shared_lock" ]' in installer
    assert 'chown "$deploy_user:$deploy_user" "$shared_lock"' in installer
    assert 'chmod 0660 "$shared_lock"' in installer
    assert installer.index('chown "$deploy_user:$deploy_user" "$shared_lock"') \
        < installer.index("/usr/bin/systemd-tmpfiles --create")


def test_editorial_fetch_and_promotion_are_split_and_sandboxed():
    fetch = (SYSTEMD / "bbb-editorial-fetch.service").read_text(encoding="utf-8")
    promote = (SYSTEMD / "bbb-editorial-promote.service").read_text(
        encoding="utf-8")
    timer = (SYSTEMD / "bbb-editorial-refresh.timer").read_text(encoding="utf-8")
    assert "User=@BBB_DEPLOY_USER@" in fetch
    assert "OnSuccess=bbb-editorial-promote.service" in fetch
    assert "editorial_fetch.py" in fetch
    assert "User=root" in promote
    assert "editorial_promote.py" in promote
    assert "/run/lock/bristolbusbot/editorial.lock" in fetch
    assert "/run/lock/bristolbusbot/editorial.lock" in promote
    assert "ProtectSystem=strict" in fetch
    assert "ProtectSystem=strict" in promote
    assert (
        "ReadWritePaths=/var/lib/bristolbusbot-editorial/incoming "
        "/var/lib/bristolbusbot/monitoring /run/lock/bristolbusbot"
    ) in fetch
    assert (
        "ReadWritePaths=/var/lib/bristolbusbot-editorial "
        "/var/lib/bristolbusbot/monitoring /run/lock/bristolbusbot"
    ) in promote
    assert "OnCalendar=*:0/30" in timer
    assert "Persistent=true" in timer
    tmpfiles = (SYSTEMD.parent / "tmpfiles" / "bristolbusbot.conf").read_text(
        encoding="utf-8")
    assert (
        "f /run/lock/bristolbusbot/editorial.lock "
        "0660 @BBB_DEPLOY_USER@ @BBB_DEPLOY_USER@ -"
    ) in tmpfiles
    assert (
        "z /run/lock/bristolbusbot/editorial.lock "
        "0660 @BBB_DEPLOY_USER@ @BBB_DEPLOY_USER@ -"
    ) in tmpfiles
    assert (
        "d /var/lib/bristolbusbot-editorial "
        "0750 root @BBB_DEPLOY_USER@ -"
    ) in tmpfiles
    assert (
        "d /var/lib/bristolbusbot-editorial/incoming "
        "0750 @BBB_DEPLOY_USER@ @BBB_DEPLOY_USER@ -"
    ) in tmpfiles

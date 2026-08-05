#!/bin/sh
# One-time, idempotent bootstrap for the release/symlink deployment layout.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "run this installer through sudo" >&2
    exit 1
fi

stage=${1:-}
test -n "$stage"
test -d "$stage/systemd"
test -f "$stage/deploy_control.sh"

deploy_user=@BBB_DEPLOY_USER@
remote_home=@BBB_REMOTE_HOME@
base=@BBB_DEPLOY_BASE@
current=$base/current
releases=$base/releases
incoming=$base/incoming
backup=/var/backups/bristolbusbot-unified-deploy-$(date -u +%Y%m%dT%H%M%SZ)
marker=/etc/bristolbusbot/unified-deploy-layout
social_config=/etc/bristolbusbot/social.env
social_legacy_db=/var/lib/bristolbusbot/social.db
social_state_dir=/var/lib/bristolbusbot/social
social_db=$social_state_dir/social.db
enrichment_dir=/var/lib/bristolbusbot/enrichment
social_db_migrated=0
social_timer_enabled=0
changed=0

mkdir -p "$current" "$releases" "$incoming" "$backup/units" \
    "$backup/new-units" "$backup/files"
chown -R "$deploy_user:$deploy_user" "$base"
chmod 0750 "$base" "$current" "$releases" "$incoming"

bootstrap_link() {
    name=$1
    legacy=$2
    link=$current/$name
    test -d "$legacy"
    if [ ! -e "$link" ] && [ ! -L "$link" ]; then
        ln -s "$legacy" "$link"
    fi
    test -L "$link"
    test -d "$link"
}

bootstrap_link collector "$remote_home/bbb-collector"
bootstrap_link site "$remote_home/bbb-site"
bootstrap_link bot "$remote_home/bbb-bot"
bootstrap_link pipeline "$remote_home/bus-audit"

for component in collector site bot pipeline tunnel; do
    /usr/bin/python3 "$stage/validate_production_config.py" "$component"
done

/usr/bin/python3 "$stage/verify_release.py" --help >/dev/null
/usr/bin/python3 "$stage/timetable_control.py" validate >/dev/null
/usr/bin/python3 "$stage/editorial_context.py" \
    --file "$stage/editorial-context.json" >/dev/null
/usr/bin/python3 -m py_compile "$stage/timetable_delivery.py" "$stage/timetable_promote.py" \
    "$stage/timetable_service_profile.py" \
    "$stage/timetable_manifest.py" "$stage/timetable_editions.py" \
    "$stage/run_recorded_job.py" "$stage/aggregate_health.py" "$stage/sample_resources.py" \
    "$stage/data_health.py" \
    "$stage/configure_timetable_delivery.py" "$stage/configure_social_curation.py" \
    "$stage/enrichment_layout.py" \
    "$stage/editorial_context.py" \
    "$stage/editorial_fetch.py" "$stage/editorial_promote.py"
/usr/bin/systemd-analyze verify "$stage/systemd"/*.service "$stage/systemd"/*.timer

for unit in "$stage/systemd"/*.service "$stage/systemd"/*.timer; do
    name=$(basename "$unit")
    if [ -f "/etc/systemd/system/$name" ]; then
        cp -p "/etc/systemd/system/$name" "$backup/units/$name"
    else
        : > "$backup/new-units/$name"
    fi
done

backup_file() {
    destination=$1
    name=$(basename "$destination")
    if [ -f "$destination" ]; then
        cp -p "$destination" "$backup/files/$name"
        printf '%s %s\n' "$name" "$destination" >> "$backup/file-map"
    else
        printf '%s\n' "$destination" >> "$backup/new-files"
    fi
}

for destination in \
    /usr/local/sbin/bbb-deploy-control \
    /usr/local/sbin/bbb-timetable-control \
    /usr/local/libexec/bbb-validate-config \
    /usr/local/libexec/bbb-verify-release \
    /usr/local/libexec/bbb-verify-collector-state \
    /usr/local/libexec/bbb-audit-rollup \
    /usr/local/libexec/bbb-run-recorded-job \
    /usr/local/libexec/bbb-aggregate-health \
    /usr/local/libexec/bbb-sample-resources \
    /usr/local/libexec/bbb-data-health \
    /usr/local/libexec/bbb-enrichment-layout \
    /usr/local/sbin/bbb-configure-timetable-delivery \
    /usr/local/sbin/bbb-configure-social-curation \
    /usr/local/libexec/bristolbusbot-timetable/timetable_delivery.py \
    /usr/local/libexec/bristolbusbot-timetable/timetable_promote.py \
    /usr/local/libexec/bristolbusbot-timetable/timetable_service_profile.py \
    /usr/local/libexec/bristolbusbot-timetable/timetable_manifest.py \
    /usr/local/libexec/bristolbusbot-timetable/timetable_editions.py \
    /usr/local/libexec/bristolbusbot-timetable/timetable_control.py \
    /usr/local/libexec/bristolbusbot-editorial/editorial_context.py \
    /usr/local/libexec/bristolbusbot-editorial/editorial_fetch.py \
    /usr/local/libexec/bristolbusbot-editorial/editorial_promote.py \
    /etc/sudoers.d/bristolbusbot-deploy \
    /etc/tmpfiles.d/bristolbusbot.conf \
    /etc/bristolbusbot/backup.json \
    "$enrichment_dir/fbribuses.json" \
    "$enrichment_dir/stop_localities.json" \
    "$enrichment_dir/stop_enrichment.json" \
    "$enrichment_dir/local_flavour.json" \
    "$enrichment_dir/route_details.json" \
    "$social_config" \
    "$remote_home/bus-audit/publish_to_github.sh"
do
    backup_file "$destination"
done

rollback() {
    code=$?
    trap - EXIT INT TERM
    if [ "$changed" -eq 1 ]; then
        /usr/bin/systemctl stop bbb-social-curation.timer \
            bbb-social-curation.service >/dev/null 2>&1 || true
        if [ "$social_db_migrated" -eq 1 ]; then
            for suffix in '' -wal -shm; do
                if [ -e "$social_db$suffix" ] && \
                   [ ! -e "$social_legacy_db$suffix" ]; then
                    mv "$social_db$suffix" "$social_legacy_db$suffix" || true
                fi
            done
        fi
        for unit in "$backup/new-units/"*; do
            test -e "$unit" || continue
            name=$(basename "$unit")
            /usr/bin/systemctl disable --now "$name" >/dev/null 2>&1 || true
            rm -f "/etc/systemd/system/$name"
        done
        cp -p "$backup/units/"* /etc/systemd/system/ 2>/dev/null || true
        if [ -f "$backup/file-map" ]; then
            while read -r name destination; do
                cp -p "$backup/files/$name" "$destination" || true
            done < "$backup/file-map"
        fi
        if [ -f "$backup/new-files" ]; then
            while read -r destination; do
                rm -f "$destination"
            done < "$backup/new-files"
        fi
        /usr/bin/systemctl daemon-reload || true
        /usr/bin/systemctl restart bbb-collector.service bbb-site.service bbb-bot.service bbb-tunnel.service || true
        if [ "$social_timer_enabled" -eq 1 ]; then
            /usr/bin/systemctl start bbb-social-curation.timer || true
        fi
    fi
    echo "unified deploy installation failed; previous units were restored" >&2
    exit "$code"
}
trap rollback EXIT INT TERM

wait_collector() {
    tries=0
    while [ "$tries" -lt 18 ]; do
        if /usr/local/libexec/bbb-verify-collector-state --max-poll-age 180 >/dev/null 2>&1; then
            return 0
        fi
        tries=$((tries + 1))
        sleep 5
    done
    return 1
}

wait_site() {
    tries=0
    while [ "$tries" -lt 30 ]; do
        if /usr/bin/python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:5002/healthz", timeout=10)); assert d.get("status") in ("ok", "warn")' >/dev/null 2>&1; then
            return 0
        fi
        tries=$((tries + 1))
        sleep 2
    done
    return 1
}

wait_bot() {
    tries=0
    while [ "$tries" -lt 30 ]; do
        if /usr/local/libexec/bbb-enrichment-layout validate --quiet >/dev/null 2>&1 && \
           /usr/bin/python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:3010/api/health", timeout=10)); h=d["details"]["healthData"]; e=h["application"]["editorialContext"]; assert d.get("success") is True and d.get("runtime") == "systemd" and e.get("loaded") is True and h["database"]["timetable"]["connected"] is True and h["database"]["appData"]["connected"] is True and h["application"]["state"]["busDetailsLoaded"] > 0' >/dev/null 2>&1; then
            return 0
        fi
        tries=$((tries + 1))
        sleep 2
    done
    return 1
}

wait_public_site() {
    tries=0
    while [ "$tries" -lt 15 ]; do
        if /usr/bin/curl -fsS --max-time 10 https://bristolbuses.live/healthz >/dev/null 2>&1; then
            return 0
        fi
        tries=$((tries + 1))
        sleep 2
    done
    return 1
}

changed=1
if /usr/bin/systemctl is-enabled --quiet bbb-social-curation.timer; then
    social_timer_enabled=1
fi
/usr/bin/systemctl stop bbb-social-curation.timer \
    bbb-social-curation.service >/dev/null 2>&1 || true
install -o "$deploy_user" -g "$deploy_user" -m 0750 -d "$social_state_dir"
if [ -e "$social_legacy_db" ] || [ -L "$social_legacy_db" ]; then
    if [ -e "$social_db" ] || [ -L "$social_db" ]; then
        echo "both legacy and durable social databases exist" >&2
        exit 1
    fi
    if [ -L "$social_legacy_db" ] || [ ! -f "$social_legacy_db" ]; then
        echo "legacy social database is not a regular file" >&2
        exit 1
    fi
    for suffix in -wal -shm; do
        if [ -L "$social_legacy_db$suffix" ]; then
            echo "legacy social database sidecar is a symlink" >&2
            exit 1
        fi
        if [ -e "$social_legacy_db$suffix" ]; then
            test -f "$social_legacy_db$suffix"
        fi
    done
    mv "$social_legacy_db" "$social_db"
    social_db_migrated=1
    for suffix in -wal -shm; do
        if [ -e "$social_legacy_db$suffix" ]; then
            mv "$social_legacy_db$suffix" "$social_db$suffix"
        fi
    done
    chown "$deploy_user:$deploy_user" "$social_db"
    for suffix in -wal -shm; do
        if [ -e "$social_db$suffix" ]; then
            chown "$deploy_user:$deploy_user" "$social_db$suffix"
        fi
    done
    chmod 0600 "$social_db"
fi
if [ -e "$social_config" ] || [ -L "$social_config" ]; then
    /usr/bin/python3 - "$social_config" <<'PY'
import os
import stat
import sys
from pathlib import Path

path = Path(sys.argv[1])
info = path.lstat()
if not stat.S_ISREG(info.st_mode):
    raise SystemExit("social configuration is not a regular file")
old = "BBB_SOCIAL_DB=/var/lib/bristolbusbot/social.db\n"
new = "BBB_SOCIAL_DB=/var/lib/bristolbusbot/social/social.db\n"
text = path.read_text(encoding="utf-8")
old_count = text.count(old)
new_count = text.count(new)
if (old_count, new_count) == (1, 0):
    candidate = path.with_name(".social.env.migration")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(candidate, flags, stat.S_IMODE(info.st_mode))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(candidate, info.st_uid, info.st_gid)
        os.chmod(candidate, stat.S_IMODE(info.st_mode))
        os.replace(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)
elif (old_count, new_count) != (0, 1):
    raise SystemExit("social configuration has an unexpected database path")
PY
    /usr/bin/python3 "$stage/validate_production_config.py" social
fi
install -o root -g root -m 0755 "$stage/deploy_control.sh" /usr/local/sbin/bbb-deploy-control
install -o root -g root -m 0755 "$stage/timetable_control.py" /usr/local/sbin/bbb-timetable-control
install -o root -g root -m 0755 "$stage/validate_production_config.py" /usr/local/libexec/bbb-validate-config
install -o root -g root -m 0755 "$stage/verify_release.py" /usr/local/libexec/bbb-verify-release
install -o root -g root -m 0755 "$stage/verify_collector_state.py" /usr/local/libexec/bbb-verify-collector-state
install -o root -g root -m 0755 "$stage/run_audit_rollup.sh" /usr/local/libexec/bbb-audit-rollup
install -o root -g root -m 0755 "$stage/run_recorded_job.py" /usr/local/libexec/bbb-run-recorded-job
install -o root -g root -m 0755 "$stage/aggregate_health.py" /usr/local/libexec/bbb-aggregate-health
install -o root -g root -m 0755 "$stage/sample_resources.py" /usr/local/libexec/bbb-sample-resources
install -o root -g root -m 0755 "$stage/data_health.py" /usr/local/libexec/bbb-data-health
install -o root -g root -m 0755 "$stage/enrichment_layout.py" /usr/local/libexec/bbb-enrichment-layout
install -o root -g root -m 0755 "$stage/configure_timetable_delivery.py" /usr/local/sbin/bbb-configure-timetable-delivery
install -o root -g root -m 0755 "$stage/configure_social_curation.py" /usr/local/sbin/bbb-configure-social-curation
install -o root -g root -m 0755 -d /usr/local/libexec/bristolbusbot-timetable
install -o root -g root -m 0755 "$stage/timetable_delivery.py" /usr/local/libexec/bristolbusbot-timetable/timetable_delivery.py
install -o root -g root -m 0755 "$stage/timetable_promote.py" /usr/local/libexec/bristolbusbot-timetable/timetable_promote.py
install -o root -g root -m 0644 "$stage/timetable_service_profile.py" /usr/local/libexec/bristolbusbot-timetable/timetable_service_profile.py
install -o root -g root -m 0644 "$stage/timetable_manifest.py" /usr/local/libexec/bristolbusbot-timetable/timetable_manifest.py
install -o root -g root -m 0644 "$stage/timetable_editions.py" /usr/local/libexec/bristolbusbot-timetable/timetable_editions.py
install -o root -g root -m 0644 "$stage/timetable_control.py" /usr/local/libexec/bristolbusbot-timetable/timetable_control.py
install -o root -g root -m 0755 -d /usr/local/libexec/bristolbusbot-editorial
install -o root -g root -m 0644 "$stage/editorial_context.py" /usr/local/libexec/bristolbusbot-editorial/editorial_context.py
install -o root -g root -m 0755 "$stage/editorial_fetch.py" /usr/local/libexec/bristolbusbot-editorial/editorial_fetch.py
install -o root -g root -m 0755 "$stage/editorial_promote.py" /usr/local/libexec/bristolbusbot-editorial/editorial_promote.py

install -o root -g root -m 0440 "$stage/sudoers/bristolbusbot-deploy" /etc/sudoers.d/bristolbusbot-deploy.new
/usr/sbin/visudo -cf /etc/sudoers.d/bristolbusbot-deploy.new
mv -f /etc/sudoers.d/bristolbusbot-deploy.new /etc/sudoers.d/bristolbusbot-deploy

install -o "$deploy_user" -g "$deploy_user" -m 0755 "$stage/publish_to_github.sh" "$remote_home/bus-audit/publish_to_github.sh"
for unit in "$stage/systemd"/*.service "$stage/systemd"/*.timer; do
    install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
install -o root -g root -m 0644 "$stage/tmpfiles/bristolbusbot.conf" /etc/tmpfiles.d/bristolbusbot.conf
for shared_lock in \
    /run/lock/bristolbusbot/heavy-io.lock \
    /run/lock/bristolbusbot/editorial.lock
do
    if [ -L "$shared_lock" ] || { [ -e "$shared_lock" ] && [ ! -f "$shared_lock" ]; }; then
        echo "unsafe shared lock path: $shared_lock" >&2
        exit 1
    fi
    if [ -e "$shared_lock" ]; then
        chown "$deploy_user:$deploy_user" "$shared_lock"
        chmod 0660 "$shared_lock"
    fi
done
/usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/bristolbusbot.conf
/usr/local/libexec/bbb-enrichment-layout migrate \
    --source "$current/bot" \
    --destination "$enrichment_dir" \
    --backup-config /etc/bristolbusbot/backup.json \
    --owner "$deploy_user" >/dev/null
if [ ! -e /var/lib/bristolbusbot-editorial/editorial-context.json ]; then
    install -o root -g "$deploy_user" -m 0640 \
        "$stage/editorial-context.json" \
        /var/lib/bristolbusbot-editorial/editorial-context.json
fi
/usr/bin/python3 /usr/local/libexec/bristolbusbot-editorial/editorial_context.py \
    --file /var/lib/bristolbusbot-editorial/editorial-context.json >/dev/null

/usr/bin/systemctl daemon-reload
/usr/bin/systemctl restart bbb-collector.service
if ! wait_collector; then echo "collector health wait exhausted" >&2; exit 1; fi
/usr/bin/systemctl restart bbb-site.service
if ! wait_site; then echo "site health wait exhausted" >&2; exit 1; fi
/usr/bin/systemctl restart bbb-bot.service
if ! wait_bot; then echo "bot health wait exhausted" >&2; exit 1; fi
/usr/bin/systemctl restart bbb-tunnel.service
/usr/bin/systemctl is-active --quiet bbb-collector.service bbb-site.service bbb-bot.service bbb-tunnel.service
if ! wait_public_site; then echo "public site health wait exhausted" >&2; exit 1; fi

if [ "$social_timer_enabled" -eq 1 ]; then
    /usr/bin/systemctl start bbb-social-curation.timer
fi

/usr/bin/systemctl enable --now bbb-editorial-refresh.timer
for timer in "$stage/systemd"/*.timer; do
    timer_name=$(basename "$timer")
    if [ "$timer_name" = bbb-timetable-shadow.timer ] && \
       ! /usr/bin/systemctl is-enabled --quiet "$timer_name"; then
        echo "Timetable shadow timer installed but left disabled until its root-only credential is configured."
        continue
    fi
    if [ "$timer_name" = bbb-social-curation.timer ] && \
       ! /usr/bin/systemctl is-enabled --quiet "$timer_name"; then
        echo "Social curation timer installed but left disabled until shadow and attended delivery pass."
        continue
    fi
    /usr/bin/systemctl is-enabled --quiet "$timer_name"
    /usr/bin/systemctl is-active --quiet "$timer_name"
done

install -o root -g root -m 0644 /dev/null "$marker"
printf '%s\n' "installed=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$marker"
changed=0
trap - EXIT INT TERM
echo "Unified deployment layout installed; all live health checks passed."

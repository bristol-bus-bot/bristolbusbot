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
/usr/bin/python3 -m py_compile "$stage/timetable_delivery.py" "$stage/timetable_promote.py" \
    "$stage/timetable_manifest.py" \
    "$stage/run_recorded_job.py" "$stage/aggregate_health.py" "$stage/sample_resources.py" \
    "$stage/configure_timetable_delivery.py"
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
    /usr/local/sbin/bbb-configure-timetable-delivery \
    /usr/local/libexec/bristolbusbot-timetable/timetable_delivery.py \
    /usr/local/libexec/bristolbusbot-timetable/timetable_promote.py \
    /usr/local/libexec/bristolbusbot-timetable/timetable_manifest.py \
    /usr/local/libexec/bristolbusbot-timetable/timetable_control.py \
    /etc/sudoers.d/bristolbusbot-deploy \
    /etc/tmpfiles.d/bristolbusbot.conf \
    "$remote_home/bus-audit/publish_to_github.sh"
do
    backup_file "$destination"
done

rollback() {
    code=$?
    trap - EXIT INT TERM
    if [ "$changed" -eq 1 ]; then
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
        if /usr/bin/python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:3010/api/health", timeout=10)); assert d.get("success") is True and d.get("runtime") == "systemd"' >/dev/null 2>&1; then
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
install -o root -g root -m 0755 "$stage/deploy_control.sh" /usr/local/sbin/bbb-deploy-control
install -o root -g root -m 0755 "$stage/timetable_control.py" /usr/local/sbin/bbb-timetable-control
install -o root -g root -m 0755 "$stage/validate_production_config.py" /usr/local/libexec/bbb-validate-config
install -o root -g root -m 0755 "$stage/verify_release.py" /usr/local/libexec/bbb-verify-release
install -o root -g root -m 0755 "$stage/verify_collector_state.py" /usr/local/libexec/bbb-verify-collector-state
install -o root -g root -m 0755 "$stage/run_audit_rollup.sh" /usr/local/libexec/bbb-audit-rollup
install -o root -g root -m 0755 "$stage/run_recorded_job.py" /usr/local/libexec/bbb-run-recorded-job
install -o root -g root -m 0755 "$stage/aggregate_health.py" /usr/local/libexec/bbb-aggregate-health
install -o root -g root -m 0755 "$stage/sample_resources.py" /usr/local/libexec/bbb-sample-resources
install -o root -g root -m 0755 "$stage/configure_timetable_delivery.py" /usr/local/sbin/bbb-configure-timetable-delivery
install -o root -g root -m 0755 -d /usr/local/libexec/bristolbusbot-timetable
install -o root -g root -m 0755 "$stage/timetable_delivery.py" /usr/local/libexec/bristolbusbot-timetable/timetable_delivery.py
install -o root -g root -m 0755 "$stage/timetable_promote.py" /usr/local/libexec/bristolbusbot-timetable/timetable_promote.py
install -o root -g root -m 0644 "$stage/timetable_manifest.py" /usr/local/libexec/bristolbusbot-timetable/timetable_manifest.py
install -o root -g root -m 0644 "$stage/timetable_control.py" /usr/local/libexec/bristolbusbot-timetable/timetable_control.py

install -o root -g root -m 0440 "$stage/sudoers/bristolbusbot-deploy" /etc/sudoers.d/bristolbusbot-deploy.new
/usr/sbin/visudo -cf /etc/sudoers.d/bristolbusbot-deploy.new
mv -f /etc/sudoers.d/bristolbusbot-deploy.new /etc/sudoers.d/bristolbusbot-deploy

install -o "$deploy_user" -g "$deploy_user" -m 0755 "$stage/publish_to_github.sh" "$remote_home/bus-audit/publish_to_github.sh"
for unit in "$stage/systemd"/*.service "$stage/systemd"/*.timer; do
    install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
install -o root -g root -m 0644 "$stage/tmpfiles/bristolbusbot.conf" /etc/tmpfiles.d/bristolbusbot.conf
/usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/bristolbusbot.conf

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

for timer in "$stage/systemd"/*.timer; do
    timer_name=$(basename "$timer")
    if [ "$timer_name" = bbb-timetable-shadow.timer ] && \
       ! /usr/bin/systemctl is-enabled --quiet "$timer_name"; then
        echo "Timetable shadow timer installed but left disabled until its root-only credential is configured."
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

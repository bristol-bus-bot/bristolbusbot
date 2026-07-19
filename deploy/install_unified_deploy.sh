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

mkdir -p "$current" "$releases" "$incoming" "$backup/units"
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
/usr/bin/systemd-analyze verify "$stage/systemd"/*.service "$stage/systemd"/*.timer

for unit in "$stage/systemd"/*.service "$stage/systemd"/*.timer; do
    name=$(basename "$unit")
    if [ -f "/etc/systemd/system/$name" ]; then
        cp -p "/etc/systemd/system/$name" "$backup/units/$name"
    fi
done
cp -p "$remote_home/bus-audit/publish_to_github.sh" "$backup/publish_to_github.sh"

rollback() {
    code=$?
    trap - EXIT INT TERM
    if [ "$changed" -eq 1 ]; then
        cp -p "$backup/units/"* /etc/systemd/system/ 2>/dev/null || true
        cp -p "$backup/publish_to_github.sh" "$remote_home/bus-audit/publish_to_github.sh" || true
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

install -o root -g root -m 0755 "$stage/deploy_control.sh" /usr/local/sbin/bbb-deploy-control
install -o root -g root -m 0755 "$stage/timetable_control.py" /usr/local/sbin/bbb-timetable-control
install -o root -g root -m 0755 "$stage/validate_production_config.py" /usr/local/libexec/bbb-validate-config
install -o root -g root -m 0755 "$stage/verify_release.py" /usr/local/libexec/bbb-verify-release
install -o root -g root -m 0755 "$stage/verify_collector_state.py" /usr/local/libexec/bbb-verify-collector-state
install -o root -g root -m 0755 "$stage/run_audit_rollup.sh" /usr/local/libexec/bbb-audit-rollup

install -o root -g root -m 0440 "$stage/sudoers/bristolbusbot-deploy" /etc/sudoers.d/bristolbusbot-deploy.new
/usr/sbin/visudo -cf /etc/sudoers.d/bristolbusbot-deploy.new
mv -f /etc/sudoers.d/bristolbusbot-deploy.new /etc/sudoers.d/bristolbusbot-deploy

install -o "$deploy_user" -g "$deploy_user" -m 0755 "$stage/publish_to_github.sh" "$remote_home/bus-audit/publish_to_github.sh"
for unit in "$stage/systemd"/*.service "$stage/systemd"/*.timer; do
    install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
install -o root -g root -m 0644 "$stage/tmpfiles/bristolbusbot.conf" /etc/tmpfiles.d/bristolbusbot.conf
/usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/bristolbusbot.conf
changed=1

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
    /usr/bin/systemctl is-enabled --quiet "$(basename "$timer")"
    /usr/bin/systemctl is-active --quiet "$(basename "$timer")"
done

install -o root -g root -m 0644 /dev/null "$marker"
printf '%s\n' "installed=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$marker"
changed=0
trap - EXIT INT TERM
echo "Unified deployment layout installed; all live health checks passed."

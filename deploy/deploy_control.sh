#!/bin/sh
# Root-only, tightly allowlisted operations used by deploy/push.py.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "bbb-deploy-control must run as root" >&2
    exit 1
fi

action=${1:-}
component=${2:-}
case "$action:$component" in
    restart:collector) exec /usr/bin/systemctl restart bbb-collector.service ;;
    restart:site) exec /usr/bin/systemctl restart bbb-site.service ;;
    restart:bot) exec /usr/bin/systemctl restart bbb-bot.service ;;
    restart:tunnel) exec /usr/bin/systemctl restart bbb-tunnel.service ;;
    bot-token-promote:)
        source=@BBB_DEPLOY_BASE@/incoming/bot.env.token-new
        target=/etc/bristolbusbot/bot.env
        candidate=/etc/bristolbusbot/bot.env.token-new
        previous=/etc/bristolbusbot/bot.env.token-previous
        test -f "$source"
        test ! -L "$source"
        test "$(stat -c %U "$source")" = @BBB_DEPLOY_USER@
        test "$(stat -c %a "$source")" = 600
        test -f "$target"
        test ! -L "$target"
        test ! -e "$previous"
        test "$(grep -c '^API_AUTH_TOKEN=' "$source")" -eq 1
        token=$(/usr/bin/sed -n 's/^API_AUTH_TOKEN=//p' "$source")
        test "${#token}" -ge 32
        case "$token" in
            *[!A-Za-z0-9_-]*)
                echo "candidate bot token contains unsupported characters" >&2
                exit 65
                ;;
        esac

        rm -f "$candidate"
        install -o root -g @BBB_DEPLOY_USER@ -m 0640 "$source" "$candidate"
        if ! /usr/local/libexec/bbb-validate-config bot --file "$candidate" >/dev/null; then
            rm -f "$candidate" "$source"
            exit 65
        fi

        changed=0
        rollback_token() {
            result=$?
            trap - EXIT INT TERM
            if [ "$changed" -eq 1 ] && [ -f "$previous" ]; then
                install -o root -g @BBB_DEPLOY_USER@ -m 0640 "$previous" "$target.new"
                mv -f "$target.new" "$target"
                /usr/bin/systemctl restart bbb-bot.service >/dev/null 2>&1 || true
            fi
            rm -f "$candidate" "$source" "$previous"
            exit "$result"
        }
        trap rollback_token EXIT INT TERM

        cp -p "$target" "$previous"
        changed=1
        mv -f "$candidate" "$target"
        rm -f "$source"
        /usr/bin/systemctl restart bbb-bot.service

        healthy=0
        tries=0
        while [ "$tries" -lt 30 ]; do
            if /usr/bin/python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:3010/api/health", timeout=5)); assert d.get("success") is True and d.get("runtime") == "systemd" and d.get("service_name") == "bbb-bot.service"' >/dev/null 2>&1; then
                healthy=1
                break
            fi
            tries=$((tries + 1))
            sleep 2
        done
        test "$healthy" -eq 1

        changed=0
        rm -f "$previous"
        trap - EXIT INT TERM
        exit 0
        ;;
    timetable-promote:)
        exec /usr/local/sbin/bbb-timetable-control promote
        ;;
    timetable-rollback:)
        exec /usr/local/sbin/bbb-timetable-control rollback
        ;;
    timetable-auto-enable:)
        target=/etc/bristolbusbot/timetable-promotion-enabled
        candidate=/etc/bristolbusbot/.timetable-promotion-enabled.new
        test -d /etc/bristolbusbot
        test ! -L /etc/bristolbusbot
        if [ -e "$target" ] || [ -L "$target" ]; then
            test -f "$target"
            test ! -L "$target"
            test "$(stat -c %U "$target")" = root
            test "$(stat -c %G "$target")" = root
            test "$(stat -c %a "$target")" = 644
            exit 0
        fi
        test ! -e "$candidate"
        test ! -L "$candidate"
        install -o root -g root -m 0644 /dev/null "$candidate"
        printf '%s\n' "enabled=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$candidate"
        mv -f "$candidate" "$target"
        exit 0
        ;;
    timetable-auto-disable:)
        target=/etc/bristolbusbot/timetable-promotion-enabled
        if [ ! -e "$target" ] && [ ! -L "$target" ]; then
            exit 0
        fi
        test -f "$target"
        test ! -L "$target"
        test "$(stat -c %U "$target")" = root
        test "$(stat -c %G "$target")" = root
        test "$(stat -c %a "$target")" = 644
        rm -f "$target"
        exit 0
        ;;
    tunnel-promote:)
        source=@BBB_DEPLOY_BASE@/incoming/tunnel-config.yml
        target=/etc/bristolbusbot/cloudflared/config.yml
        previous=/etc/bristolbusbot/cloudflared/config.yml.previous
        test -f "$source"
        test ! -L "$source"
        test "$(stat -c %U "$source")" = @BBB_DEPLOY_USER@
        /usr/local/bin/cloudflared tunnel ingress validate --config "$source"
        cp -p "$target" "$previous"
        install -o root -g @BBB_DEPLOY_USER@ -m 0640 "$source" "$target.new"
        mv -f "$target.new" "$target"
        rm -f "$source"
        exec /usr/bin/systemctl restart bbb-tunnel.service
        ;;
    tunnel-rollback:)
        target=/etc/bristolbusbot/cloudflared/config.yml
        previous=/etc/bristolbusbot/cloudflared/config.yml.previous
        test -f "$previous"
        install -o root -g @BBB_DEPLOY_USER@ -m 0640 "$previous" "$target.new"
        mv -f "$target.new" "$target"
        exec /usr/bin/systemctl restart bbb-tunnel.service
        ;;
    *)
        echo "refusing unsupported deploy-control action" >&2
        exit 64
        ;;
esac

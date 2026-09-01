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
    fleet-shadow:) exec /usr/bin/systemctl start bbb-fleet-shadow.service ;;
    fleet-promote:) exec /usr/bin/systemctl start bbb-fleet-stage.service ;;
    fleet-auto-run:) exec /usr/bin/systemctl start bbb-fleet-refresh.service ;;
    locality-shadow:) exec /usr/bin/systemctl start bbb-locality-shadow.service ;;
    locality-promote:) exec /usr/bin/systemctl start bbb-locality-stage.service ;;
    locality-auto-run:) exec /usr/bin/systemctl start bbb-locality-refresh.service ;;
    blurb-generate:) exec /usr/bin/systemctl start bbb-blurb-generate.service ;;
    blurb-promote:) exec /usr/bin/systemctl start bbb-blurb-promote.service ;;
    blurb-auto-enable:)
        target=/etc/bristolbusbot/blurb-generation-enabled
        candidate=/etc/bristolbusbot/.blurb-generation-enabled.new
        /usr/local/sbin/bbb-configure-blurb-generation \
            --owner @BBB_DEPLOY_USER@ >/dev/null
        test -f /etc/bristolbusbot/blurb.env
        test ! -L /etc/bristolbusbot/blurb.env
        test -d /etc/bristolbusbot
        test ! -L /etc/bristolbusbot
        if [ -e "$target" ] || [ -L "$target" ]; then
            test -f "$target"
            test ! -L "$target"
            test "$(stat -c %U "$target")" = root
            test "$(stat -c %G "$target")" = root
            test "$(stat -c %a "$target")" = 644
        else
            test ! -e "$candidate"
            test ! -L "$candidate"
            install -o root -g root -m 0644 /dev/null "$candidate"
            printf '%s\n' "enabled=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$candidate"
            mv -f "$candidate" "$target"
        fi
        if ! /usr/bin/systemctl enable --now bbb-blurb-generate.timer; then
            rm -f "$target"
            exit 1
        fi
        exit 0
        ;;
    blurb-auto-disable:)
        target=/etc/bristolbusbot/blurb-generation-enabled
        /usr/bin/systemctl disable --now bbb-blurb-generate.timer
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
    locality-auto-enable:)
        target=/etc/bristolbusbot/locality-refresh-enabled
        candidate=/etc/bristolbusbot/.locality-refresh-enabled.new
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
    locality-auto-disable:)
        target=/etc/bristolbusbot/locality-refresh-enabled
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
    fleet-auto-enable:)
        target=/etc/bristolbusbot/fleet-refresh-enabled
        candidate=/etc/bristolbusbot/.fleet-refresh-enabled.new
        test -d /etc/bristolbusbot
        test ! -L /etc/bristolbusbot
        if [ -e "$target" ] || [ -L "$target" ]; then
            test -f "$target"
            test ! -L "$target"
            test "$(stat -c %U "$target")" = root
            test "$(stat -c %G "$target")" = root
            test "$(stat -c %a "$target")" = 644
        else
            test ! -e "$candidate"
            test ! -L "$candidate"
            install -o root -g root -m 0644 /dev/null "$candidate"
            printf '%s\n' "enabled=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$candidate"
            mv -f "$candidate" "$target"
        fi
        if ! /usr/bin/systemctl enable --now bbb-fleet-refresh.timer; then
            rm -f "$target"
            exit 1
        fi
        exit 0
        ;;
    fleet-auto-disable:)
        target=/etc/bristolbusbot/fleet-refresh-enabled
        /usr/bin/systemctl disable --now bbb-fleet-refresh.timer
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
    carto-key-promote:)
        source=@BBB_DEPLOY_BASE@/incoming/site.env.carto-new
        target=/etc/bristolbusbot/site.env
        candidate=/etc/bristolbusbot/site.env.carto-new
        previous=/etc/bristolbusbot/site.env.carto-previous
        test -f "$source"
        test ! -L "$source"
        test "$(stat -c %U "$source")" = @BBB_DEPLOY_USER@
        test "$(stat -c %a "$source")" = 600
        test -f "$target"
        test ! -L "$target"
        test ! -e "$previous"
        test "$(grep -c '^BBB_CARTO_BASEMAP_KEY=' "$source")" -eq 1
        key=$(/usr/bin/sed -n 's/^BBB_CARTO_BASEMAP_KEY=//p' "$source")
        test "${#key}" -ge 16
        test "${#key}" -le 512
        case "$key" in
            *[!A-Za-z0-9._~-]*)
                echo "candidate CARTO key has an invalid format" >&2
                exit 65
                ;;
        esac

        rm -f "$candidate"
        install -o root -g @BBB_DEPLOY_USER@ -m 0640 "$source" "$candidate"
        if ! /usr/local/libexec/bbb-validate-config site \
                --file "$candidate" >/dev/null; then
            rm -f "$candidate" "$source"
            exit 65
        fi

        changed=0
        rollback_carto_key() {
            result=$?
            trap - EXIT INT TERM
            if [ "$changed" -eq 1 ] && [ -f "$previous" ]; then
                install -o root -g @BBB_DEPLOY_USER@ -m 0640 \
                    "$previous" "$target.new"
                mv -f "$target.new" "$target"
                /usr/bin/systemctl restart bbb-site.service >/dev/null 2>&1 || true
            fi
            rm -f "$candidate" "$source" "$previous"
            exit "$result"
        }
        trap rollback_carto_key EXIT INT TERM

        cp -p "$target" "$previous"
        changed=1
        mv -f "$candidate" "$target"
        rm -f "$source"
        /usr/bin/systemctl restart bbb-site.service

        healthy=0
        tries=0
        while [ "$tries" -lt 30 ]; do
            if /usr/bin/python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:5002/healthz", timeout=5)); assert d.get("status") in ("ok", "warn")' >/dev/null 2>&1; then
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
    social-live-enable:)
        target=/etc/bristolbusbot/social-live-enabled
        candidate=/etc/bristolbusbot/.social-live-enabled.new
        /usr/local/libexec/bbb-validate-config social >/dev/null
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
    social-live-disable:)
        target=/etc/bristolbusbot/social-live-enabled
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

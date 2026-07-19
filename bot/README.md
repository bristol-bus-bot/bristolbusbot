# Bristol Bus Bot

The TypeScript social-media component of bristolbusbot. In production it reads
corroborated, observed delay events from the shared collector's `live.db`,
selects a suitable event, generates commentary, and posts as
`@bristolbusbot.live` on Bluesky.

It does not own live-data polling or timetable matching. Those belong to
`../collector/`.

## Local development

```powershell
npm ci
npm run typecheck
npm run build
```

Copy `.env.example` to `.env` for local work. Keep `TEST_MODE=true`; never copy
production secrets from the Pi.

Important production settings are:

```text
TEST_MODE=false
INGEST_MODE=events
LIVE_DB_PATH=/var/lib/bristolbusbot/collector/live.db
PORT=3010
BSKY_HANDLE=bristolbusbot.live
```

## Production

- Current release: `~/bristolbusbot/current/bot` on the Pi
- Durable state: `/var/lib/bristolbusbot/bot/app_data.db`
- systemd unit: `bbb-bot.service`
- API: `127.0.0.1:3010`
- Deploy: `python deploy/push.py --component bot` from the repository root

The deploy builds and tests locally, installs production dependencies in a new
immutable release, atomically switches code and requires a successful health
response identifying systemd as the runtime. Pi-owned config and durable state
are never included in a release; the previous code remains the rollback target.

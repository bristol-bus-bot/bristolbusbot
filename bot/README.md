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
EDITORIAL_CONTEXT_PATH=/var/lib/bristolbusbot-editorial/editorial-context.json
EDITORIAL_USAGE_PATH=/var/lib/bristolbusbot/bot/editorial-usage.json
BBB_FLEET_JSON=/var/lib/bristolbusbot/enrichment/fbribuses.json
BBB_LOCALITIES_JSON=/var/lib/bristolbusbot/enrichment/stop_localities.json
BBB_ENRICHMENT_JSON=/var/lib/bristolbusbot/enrichment/stop_enrichment.json
BBB_LOCAL_FLAVOUR_JSON=/var/lib/bristolbusbot/enrichment/local_flavour.json
BBB_ROUTE_DETAILS_JSON=/var/lib/bristolbusbot/enrichment/route_details.json
```

## Production

Optional traffic commentary uses TomTom Flow Segment Data. Set `TOMTOM_API_KEY`
and `TRAFFIC_ENABLED=true` in the private bot environment after checking the
account's allowance. `TRAFFIC_USAGE_PATH` must point to durable writable state
(normally `/var/lib/bristolbusbot/bot/traffic-usage.json`). No key belongs in Git.
Traffic has one slot in the eight-post subject rotation; absent or unsuitable
data falls back to another subject. Requests have a five-second overall wait,
no retries, at least ten minutes between lookups, and persisted limits of 50
per UTC day and 500 per UTC month. Counts include failures. Provider responses
are not cached or archived by the traffic service. Traffic posts name TomTom.

Only recent bus GPS positions and high-confidence segments within 100 metres
are used. This is nearby road context, not a directional route match or proof
of what delayed the bus. Closed roads are omitted from this initial flow-only
integration. See [TomTom's endpoint documentation](https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-flow/flow-segment-data)
and [current pricing](https://docs.tomtom.com/pricing). Set `TRAFFIC_ENABLED=false`
and restart the bot to disable it without affecting other posting subjects.

- Current release: `~/bristolbusbot/current/bot` on the Pi
- Durable state: `/var/lib/bristolbusbot/bot/app_data.db`
- Durable enrichment: `/var/lib/bristolbusbot/enrichment/*.json`
- systemd unit: `bbb-bot.service`
- API: `127.0.0.1:3010`
- Deploy: `python deploy/push.py --component bot` from the repository root

The deploy builds and tests locally, installs production dependencies in a new
immutable release, atomically switches code and requires a successful health
response identifying systemd as the runtime. Pi-owned config, editorial
context and enrichment data are never included in a bot release; the previous
code remains the rollback target. Local development keeps working-directory
fallbacks, but production pins all five enrichment inputs to the durable
directory through the systemd unit.

Fleet model and livery enrichment is registration-first, then keyed by
`(operator, fleet code)`. A shared or reused fleet number that cannot identify
one physical vehicle fails closed to no enrichment; it can never borrow another
operator's record. The raw fleet file remains a private runtime input.

## Approved facts, occasions and news

`data/editorial-context.json` contains sourced claims and their active windows.
The bot uses at most one special hook in a post and never in consecutive posts.
Campaigns are limited per day; news has expiry, lifetime-use and cooldown
limits. Sources remain in the approved data for verification but are never
included in public posts. Usage survives restarts.

GitHub may open a PR for a recent official Department for Transport bus story.
Merging approves its exact wording; closing rejects it. The Pi checks merged
content on `main`, validates it again and accepts it only if the restarted bot
reports the exact promoted SHA-256. See `deploy/README.md`.

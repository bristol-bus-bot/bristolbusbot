# Social card drafts

This directory renders deterministic Instagram draft cards from already
published bot and audit data. It does not publish to any social network.

```powershell
npm install
npm run generate -- --input pack.json --output previews
```

The full-pack input is a JSON object with `botSaid` and `busWeek` objects. See
`examples/demo-pack.json` for the shape. The output directory contains seven
1080 x 1350 JPEGs plus `manifest.json`, which carries captions, per-slide alt
text and the source facts used for review:

1. a standalone full-bleed bot-observation card;
2. the weekly 100-square headline;
3. actual performance against WECA's targets;
4. the weekly day-by-day detail;
5. the weekly delay distribution; and
6. electric versus diesel / other readings and punctuality; and
7. the same week's results separated by operator.

Files 2–7 are one ordered Instagram carousel, not separate posts. Every card
names its operator; a whole-network pack is labelled `WECA network` rather
than implying that its result belongs to First Bristol or another company.

For production-shaped input, first run `build_pack.py` against the published
`audit_data.json`, the bot's loopback `/api/recent-posts` response and, when
available, `--audit-db` pointing at the collector's local audit database. A
complete weekly carousel requires `--audit-db`; it supplies the aggregate
delay distribution used by slide 4. The builder
uses the operator selected in `audit_data.json`, requires seven consecutive
rollups, at least 1,000 readings and a
successful bot post with exact journey, stop and event-time provenance.
The audit database supplies up to 20 real recent observations at the selected
stop for the quote card's receipt strip. Without it, the card explicitly shows
only the current observation; the renderer never invents comparison dots.

```powershell
python build_pack.py --audit-json audit_data.json `
  --recent-posts-json recent-posts.json --audit-db audit.db `
  --output pack.json
npm run generate -- --input pack.json --output previews
```

To render only the established Bot Said card, pass `--card bot-said`. In this
mode the input needs only `generatedAt` and `botSaid`; no weekly audit pack is
required. Long quotes shrink through bounded font sizes and are refused if
they cannot fit legibly. They are never truncated.

```powershell
python build_pack.py --app-db app_data.db `
  --post-uri at://did:plc:BOT/app.bsky.feed.post/RKEY `
  --post-url https://bsky.app/profile/bristolbusbot.live/post/RKEY `
  --audit-db audit.db --output single-card.json
npm run generate -- --input single-card.json --output previews `
  --card bot-said
```

Pass `--operator ALL` for a whole-network carousel, or another published
operator code to override the audit JSON selection. Weekly cards carry WECA's
latest published annual area-wide target, the observed shortfall, and the
long-term 95% by 2030 goal.

Gemini is not involved in rendering or numbers. An optional caption-writing
step can be added later, but its output must remain a review-only suggestion.

## Slack curation

`curation.py` implements the outbound-only flow locally. It accepts one
`bsky.app` post link from one allowlisted user in one private, non-shared
channel, resolves the actor to its DID, verifies the public post still exists,
and then looks up the exact full AT URI in `app_data.db`. Slack message text is
never used as card or caption copy. A separate `social.db` records render and
Slack delivery attempts; it is a delivery ledger, not a claim that anything
was posted to Instagram.

An attended local link render makes no Slack API call and is useful before the
Pi setup gate:

```powershell
python curation.py --db social.db --app-db app_data.db `
  --audit-db audit.db --output-dir cards `
  --channel-id C_PRIVATE --allowed-user-id U_MAINTAINER `
  --link https://bsky.app/profile/bristolbusbot.live/post/RKEY
```

Intentional regeneration requires an explicit new template identity, for
example `--new-version bot-said-v2`. Re-sharing the same link under the normal
version reuses the original delivery instead of uploading a duplicate.

The existing incoming webhook cannot upload images. The later Pi setup needs a
narrow Slack app token with `groups:history`, `groups:read`, `chat:write`,
`files:write` and `files:read`, and the app must be invited only to the private
curation channel. `files:read` is needed to reconcile an uncertain upload
before retrying. The token belongs in a root-only systemd credential file; it
must never be committed, pasted into chat or logged. Live polling will use:

```text
python curation.py ... --slack-credential /run/credentials/slack-token
```

The local code and tests do not configure the Slack app, contact Slack, install
the Pi service, or post to Instagram. Those remain attended rollout gates.

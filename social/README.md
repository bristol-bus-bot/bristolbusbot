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

The poller silently ignores ordinary channel messages and file uploads. This
lets completed cards and weekly carousels live in the same private channel
without the bot replying to each draft. A message containing more than one
`bsky.app` post link still receives a closed, explanatory refusal.

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

The first production poll deliberately records the current Slack time and
returns without reading channel history. Share the test link only after that
checkpoint-seeding run; a new installation can therefore never replay the
Free plan's retained history.

## Pi rollout and operation

The ARM64 renderer gate passed on 4 August 2026: a temporary Pi checkout
installed the native `sharp` and `resvg` packages and produced a valid
1080 x 1350 JPEG. The temporary directory was removed and Slack was not
contacted.

Production status (4 August 2026): PRs #32, #35, #36, #37 and #38 are merged,
release `20260804t205910782323z-b4980e79` and the reviewed systemd layout are
installed, and the release passed its native ARM64 render gate. The private
Slack app, allowlisted channel/user and root-only credential are configured.
Checkpoint seeding, the reviewed 1080 x 1350 shadow render and one attended
live delivery passed. Slack's API read-back confirmed the JPEG, alt-text reply
and caption reply in the correct private thread. The live marker is present
and the three-minute timer is enabled and active. Its first automatic firing
completed successfully without duplicating the request, file or replies.
Instagram posting remains manual.

Deploy the reviewed code first. This runs the complete local gates, installs
native packages off to the side and accepts the release only after another
ARM64 render. It does not start the curation job or contact Slack:

```powershell
python deploy/push.py --component social
python deploy/push.py --install-layout
```

The layout installs `bbb-social-curation.service` and its three-minute timer,
but leaves the timer disabled. The service is a sandboxed oneshot. It can read
only `app_data.db` and `audit.db`, and can write only the delivery ledger at
`/var/lib/bristolbusbot/social/social.db`, rendered cards under
`/var/lib/bristolbusbot/social/`, and its monitoring job record. The existing
backup configuration already names the ledger path.

Create or update the Slack app with only `groups:history`, `groups:read`,
`chat:write`, `files:write` and `files:read`, install it to the workspace and
invite it only to the private curation channel. On the Pi, enter the resulting
bot token directly into the hidden prompt; never put it in a command, chat,
repository file or log:

```sh
sudo /usr/local/sbin/bbb-configure-social-curation \
  --channel-id C_PRIVATE --allowed-user-id U_MAINTAINER
```

The helper writes a root-owned environment file and a mode-0600 token file.
systemd supplies the token through its private credentials directory. The
service defaults to **shadow** because `/etc/bristolbusbot/social-live-enabled`
does not exist.

Run the first poll before sharing a link; it checks the private-channel gate,
seeds the current-time checkpoint and processes no history:

```sh
sudo systemctl start bbb-social-curation.service
sudo journalctl -u bbb-social-curation.service -n 50 --no-pager
```

Then share one bot Bluesky link in the private channel and start the service
again. Shadow mode reads and verifies the request and renders locally, but
cannot reply or upload. Inspect the job record, ledger and rendered JPEG before
the attended live test.

Slack can encode one pasted URL as `<target|the same target>`. The parser uses
the actual target once and ignores the display label, so that normal phone
share format is accepted without weakening the one-link rule. Two separately
supplied links are still refused.

The external file-upload ticket and completion calls use URL-encoded form
fields, matching Slack's file-upload guide and SDK behaviour. Ordinary Slack
message calls continue to use JSON.

For that single attended test, enable live mode through the fixed allowlisted
helper, share the test link again as a new Slack message, start one job, then
verify the Slack image, alt text, caption and ledger. The shadow request is
already checkpointed, so merely rerunning the service without a new message
correctly does nothing:

```sh
sudo -n /usr/local/sbin/bbb-deploy-control social-live-enable
sudo systemctl start bbb-social-curation.service
```

Only after that passes should the timer be enabled:

```sh
sudo systemctl enable --now bbb-social-curation.timer
```

The immediate kill switch leaves every collector/site/bot/tunnel path alone:

```sh
sudo systemctl disable --now bbb-social-curation.timer
sudo -n /usr/local/sbin/bbb-deploy-control social-live-disable
```

Instagram publishing remains a manual phone action. The ledger proves only
that Slack received a draft; it never claims the image was posted to Instagram.

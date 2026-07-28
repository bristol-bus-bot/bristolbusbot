# Social card drafts

This directory renders deterministic Instagram draft cards from already
published bot and audit data. It does not publish to any social network.

```powershell
npm install
npm run generate -- --input pack.json --output previews
```

The input is a JSON object with `botSaid` and `busWeek` objects. See
`examples/demo-pack.json` for the shape. The output directory contains four
1080 x 1350 JPEGs plus `manifest.json`, which carries captions, per-slide alt
text and the source facts used for review:

1. a standalone full-bleed bot-observation card;
2. the weekly 100-square headline;
3. the weekly day-by-day detail; and
4. the weekly delay distribution.

Files 2–4 are one ordered Instagram carousel, not three separate posts.

For production-shaped input, first run `build_pack.py` against the published
`audit_data.json`, the bot's loopback `/api/recent-posts` response and, when
available, `--audit-db` pointing at the collector's local audit database. A
complete weekly carousel requires `--audit-db`; it supplies the aggregate
delay distribution used by slide 4. The builder
requires seven consecutive network rollups, at least 1,000 readings and a
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

Gemini is not involved in rendering or numbers. An optional caption-writing
step can be added later, but its output must remain a review-only suggestion.

## Slack delivery

During laptop testing, the maintainer can upload the rendered JPEGs directly
to the existing Slack DM. For unattended Pi delivery, the existing incoming webhook
can announce that a pack exists but cannot upload its image files. The minimal
future setup is a narrowly scoped Slack app token with `files:write`, stored in
a root-readable credential file, using Slack's external-upload flow. The token
and generated packs must not be committed to Git.

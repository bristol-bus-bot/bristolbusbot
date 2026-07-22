# Instagram and Threads design proposal

Status: proposed and explicitly deferred until Phases B-F of
`DATA_REFRESH_AUTOMATION.md` are complete. This document expands the
social-media items in
[`docs/ROADMAP.md`](../ROADMAP.md) into editorial principles, technical
constraints and acceptance criteria. It describes intended behaviour rather
than currently deployed functionality.

## 1. Strategy summary

The two platforms serve different purposes:

- **Threads**: a curated extension of Bluesky. It reuses existing posts
  exactly after Bluesky succeeds, with no additional BODS collection or
  Gemini generation.
- **Instagram**: a visual editorial product explaining what Bristol's buses
  have been doing through useful statistics, local context and dry humour.

Primary audience: everyday Bristol passengers rather than transport-industry
specialists.

Editorial promise: *"What Bristol's buses actually did, explained visually —
with data, local knowledge and dry humour."*

Pilot approach: review every Instagram post for 30 days. Automate only
templates that prove accurate and worthwhile.

**Core principle: social-media failures must never affect data collection,
the live site or existing Bluesky publishing.** The social pipeline therefore
runs as a separate `bbb-social` process with its own `social.db`; it is not a
new code path inside the always-on bot process.

## 2. Content and engagement plan

### Threads

Threads must not mirror every Bluesky post (~70/day). Instead:

- At most one qualifying post in any rolling 60-minute period (max 24/day,
  without forcing a post every hour).
- Reuse the exact final Bluesky text and event record. No second Gemini,
  BODS or recent-events request; publishing is one extra Meta API call.
- Only enqueue after Bluesky has returned a successful source URI. That URI
  is the immutable idempotency key; a failed Threads call cannot change the
  source Bluesky result.
- Require significance level 2 or higher.
- Exclude `lowConfidence` events. Do not repeat the same operator+route key
  within three rolling hours.
- From 00:00–05:59, publish only: delays ≥ 15 minutes, early running
  ≥ 10 minutes, or verified network disruption. This gate uses explicit
  Europe/London civil time; cadence/cooldowns use UTC instants and have DST
  tests.
- Candidates expire after 90 minutes. When several are eligible, publish the
  highest significance and then the newest; never fill an empty hour with an
  old event.
- Skip an hour if nothing is interesting enough.
- Keep replies manual during the pilot.
- Pin an introductory post: automated account, independent of operators,
  based on public transport data.
- Apart from that introductory post, do not create manual Threads originals
  during the pilot: every post must be an exact, attributable Bluesky mirror.

### Instagram positioning

Instagram does not replace the live site. Its role is to make Bristol bus
performance understandable and shareable. The main feed uses native branded
cards, not screenshots of Bluesky/Threads. An amusing post is recreated as a
Bristol Buses LIVE quote card plus a factual context card. Raw screenshots
only occasionally in Stories (max two per week).

### Pilot publishing calendar (4 feed posts/week)

| Time | Feature | Purpose |
|---|---|---|
| Monday 18:30 | Bristol Bus Week | Five-slide summary of the previous Mon–Sun |
| Wednesday 18:30 | Route Under the Microscope | 4–6 slide examination of one route |
| Friday 17:30 | The Bot Said | The week's best bot observation, with factual context |
| Sunday 11:00 | Fleet and City / Something Went Right | Alternate a licensed photo feature with a constructive data story |

Draft early enough for meaningful review: Monday at 07:30; Wednesday's draft
on Tuesday; Friday's draft on Thursday; Sunday's draft on Friday. Each feature
has a scheduled cut-off, after which an unapproved draft expires and stays
unpublished — never post stale material later. If no candidate clears its
data/editorial gates, skip the slot. Review timing after eight weeks using the
account's own Insights.

### Content templates

**Bristol Bus Week**: clear headline; network on-time % and sample size;
morning vs evening; one genuine concern and one thing that went well;
methodology, source, single call to action.

**Route Under the Microscope**: route number, endpoints, plain-English
headline; seven-day punctuality, median delay, observation count;
morning/evening comparison; map or geographic distribution; optional reviewed
Flickr photograph; "Does this match your experience?". No public route league
tables during the pilot — comparisons mislead when routes differ in
frequency, geography and coverage.

**The Bot Said**: the exact previously published bot text as a branded quote
card; route, stop, time and measured delay; optional explanation of why the
situation was unusual. Jokes target the situation or system — never
passengers, drivers or identifiable staff.

**Something Went Right**: at least one in four feed posts is constructive —
a consistently punctual route, strong evening performance, recovery after
disruption, a well-served area, an older vehicle having an excellent week.
Prevents the account becoming an uninterrupted complaint channel.

Every Sunday is constructive. Alternate a positive Fleet and City photograph
feature with a data-led Something Went Right feature. This guarantees the
one-in-four rule rather than leaving it to editorial memory.

### Stories and Reels

- Three to five Story sets per week: feed resharing, route polls, delay
  guesses, short bot cards, occasional verified major-disruption updates.
  Interactive stickers and links added manually.
- Two Reels per month during the pilot. The system may prepare a 9:16 cover,
  statistics and script; a person supplies/approves footage and assembles.
  Trial Reels can test material with non-followers first.
- Automated video production is excluded from the first release.

### Editorial and data safeguards

- Never infer delay cause unless supported by a verified SIRI-SX record.
- State the audit definition of on time: 60s early to 359s late.
- Describe the population accurately: “timing-point departures/readings we
  measured”, never “buses on time”. Publish observation count, through-date,
  method link and the frequent-service caveat; hide material sourced from a
  rollup more than 48 hours stale.
- Expected-vs-observed figures are a coverage proxy, never "cancellations".
- Weekly/monthly values are calculated from summed counts and histogram bins,
  never averages of daily percentages or medians.
- Numbers, qualifications, dates, credits, methodology: deterministic.
  Gemini may provide one optional humour hook, never generate or alter facts.
- One call to action per post. Useful alt text on every slide. Never
  communicate status through colour alone.
- Check comments twice on publishing days; no automated replies.

Minimum data gates (skip the feature if a gate fails):

- Weekly report: all seven daily rollups and ≥ 1,000 in-gate observations.
- Route report: ≥ 4 service days, 200 in-gate readings, 50 observed trips.
- Geographic or fleet feature: ≥ 200 applicable readings.

### Flickr use

A reviewed source, not an automatic illustration service:

- Search by exact registration or fleet number plus "Bristol".
- Allow only CC0, Public Domain Mark, CC BY 2.0, CC BY 4.0, or explicit
  written permission. Exclude ARR, NC, ND and SA during the pilot
  (conservative policy assumes possible later monetisation).
- Recheck the licence immediately before approval. A person confirms the
  bus, photograph and crop.
- Never imply an archive photo depicts the vehicle in a particular delay:
  label "Archive photograph — not the vehicle involved" unless it is.
- Avoid photos prominently showing identifiable passengers, children or
  drivers.
- Full attribution (title, photographer, original page, licence, crop note)
  on the final carousel slide, in the caption, and at a permanent
  `bristolbuses.live/credits/{postId}` page.
- Store the Instagram media ID against the source photo for fast takedown.
- Roughly one Flickr-led feed post every two weeks.
- Include Flickr's exact required API/product notice wherever its terms
  require it, in addition to the Creative Commons attribution. Archive the
  licence/permission evidence and attribution payload at approval time.
- Cache unapproved candidates for no more than seven days, then refetch and
  recheck. Route any owner/licensor removal request immediately to
  the configured alert channel, map it to every derived asset/post, and complete removal of
  the photo and retained Flickr data within 24 hours.
- Before any monetisation or other arguably commercial use, obtain the
  appropriate Flickr commercial API key/permission; the conservative licence
  allowlist alone does not settle API-terms status.

## 3. Technical implementation

### Shared publishing envelope

One platform-neutral event object:

```
SocialPostEnvelope
- schemaVersion
- contentId
- collectorEventId
- sourceBlueskyUri
- finalText
- createdAtUtc
- operator
- route
- direction
- vehicle
- eventType
- delaySeconds
- stop
- significance
- source
- corroboration
- lowConfidence
```

`collectorEventId`, `operator`, `source`, corroboration, confidence and the raw
signed delay seconds must be preserved by the collector-to-bot event mapping;
the handoff must not reconstruct them from prose.

`bbb-social` owns a separate `social.db`. Its delivery record is unique on
`(sourceBlueskyUri, platform)` and records status, attempt count, timestamps,
request/idempotency metadata and external post ID. States include queued,
sending, delivered, failed, expired and **unknown**. An ambiguous timeout
enters unknown and is reconciled with the platform before any retry; never
blindly retry a request that may already have published.

```
Collector event
    → existing post selection and wording
    → Bluesky delivery succeeds and final URI is recorded
    → best-effort SocialPostEnvelope handoff to bbb-social
        → Threads eligibility gate and independent delivery
        → Instagram candidate/draft pipeline
```

The bot handoff is best-effort only after Bluesky success. A failed handoff or
a killed/unhealthy `bbb-social` process can lose/delay the optional mirror but
can never delay, suppress or relabel Bluesky. `bbb-social` runs with concurrency
one for publishing; automated video remains out of scope.

Use a separate Meta app configured for the Threads use case, OAuth 2, minimum
permissions, `auto_publish_text=true` for text posts. Pin and record the Graph/
Threads API version. Store the long-lived token with log redaction and atomic
replacement; refresh around day 45 of its 60-day life and alert at 14, 7 and 2
days before expiry.

### Instagram planning and review

`InstagramDraft` record: draftId, monotonically increasing revision, template/
pillar, source date range and source-data hash, caption, alt text per asset,
rendered media references and hashes, photograph credit/licence evidence,
scheduled/published timestamps, status (draft / approved / scheduled /
publishing / published / failed / unknown / rejected / expired), approval
revision/hash plus approver ID/time, Meta container and published-media IDs.
Any caption, alt-text, media, data or schedule edit creates a revision and
invalidates the previous approval.

Any future operator UI must be loopback-only and authenticated; it may
list/preview drafts, approve/reject, edit captions and alt text, reschedule and
inspect failures and external IDs. It must not revive or publish the removed
command-centre page. For the first 30 days approval is technically required —
no draft can enter the publishing state without a recorded approval.

**Slack approval flow** (primary day-to-day interface and canonical approval
record):

- When a draft is generated, post a card to the configured review channel: preview images
  uploaded directly to Slack via `files.getUploadURLExternal` followed by
  `files.completeUploadExternal` (never the retired `files.upload` method;
  draft media stays otherwise unexposed), caption, alt text, data-gate results, schedule time, and
  Approve / Reject buttons plus an optional link to the protected operator view.
- Interactivity via **Socket Mode** (free tier; the Pi connects outward, so
  no public endpoint is added). Button presses are accepted only when team ID,
  channel ID and owner's user ID match configured allowlists, verified server-
  side; any mismatch is ignored and logged.
- An approval or rejection writes to the `InstagramDraft` record and the
  card is updated in place to show the outcome. The database is the system
  of record — free-tier Slack history expires after 90 days, so nothing may
  depend on the Slack message surviving.
- Approval actions are idempotent: pressing Approve twice, or after a
  dashboard decision, changes nothing and says so. Each button carries the
  draft revision/hash; a stale card is rejected and directs the owner to the
  current revision.
- If Slack is down or the token has expired, drafts are simply approved in
  the dashboard; Slack unavailability can never block or delay publishing
  decisions, only make them less convenient.
- Publishing outcomes (published / failed), weekly insights summaries and
  Flickr takedown requests post to the same channel.
- Factual correction/retraction is a first-class workflow: freeze affected
  drafts, record the correction, update/delete supported platform posts and
  alert the maintainer. If a source Bluesky post is deleted for factual reasons, delete
  its Threads mirror; never silently leave the copied claim live.

### Visual generation

- SVG templates using the site's existing road-sign, daylight and
  LED-inspired colour tokens.
- Render with `sharp` (official Linux ARM64 support; smoke-test on the Pi
  before deployment promotion).
- Outputs: 1080×1350 JPEG for 4:5 feed/carousels; 1080×1920 for Stories and
  Reel covers. 4:5 is safely inside Instagram's accepted 1.91:1–3:4 range.
- Bundle required open fonts; do not rely on the Pi's installed fonts.
- Golden-image tests to detect accidental layout changes.
- Route/geographic graphics use project-held route shapes and appropriately
  attributed Open Government Licensed base data. Do not render screenshots of
  the live Carto map tiles into social assets.

### Instagram publishing

- Confirm the account is professional (Business/Creator); the Instagram
  Login API works without a linked Facebook Page.
- For the project's own professional account, begin with Instagram Login and
  Standard Access; request Advanced Access only if the app later serves
  accounts not owned/managed by the project. Keep the Instagram app/token
  separate from Threads and pin the API version.
- Permissions: `instagram_business_basic`,
  `instagram_business_content_publish`, `instagram_business_manage_insights`.
- Flow: create and persist child containers → carousel/parent container →
  poll readiness → `media_publish` → record media ID. Reconcile ambiguous
  timeouts against Meta before retrying (no duplicate posts).
- Create short-lived Instagram media containers close to the approved publish
  time, not when an early draft is generated; recreate expired containers from
  the approved, hash-matched assets.
- First release automates only approved feed images/carousels; Stories and
  Reels stay manual.

### Media, OAuth and secrets

- Durable operational files live outside deploy-managed code under
  `/var/lib/bristolbusbot/social`; configuration and secrets live under
  `/etc/bristolbusbot`. The `bbb-social` service alone receives the minimum
  write/read access required.
- Draft previews stay behind the protected dashboard. After approval, images
  are exposed at opaque `/media/social/{assetId}.jpg` URLs (Meta must fetch
  publicly): no directory listing, `X-Robots-Tag: noindex`, assets removed
  48 hours after publication, path-traversal safe.
- Public OAuth callbacks route through the existing public site/tunnel ingress
  to the loopback-bound core bot service; do not create a separate public
  administration surface. Validate a short-lived single-use state value.
- Meta tokens in a dedicated root-owned/sealed file under
  `/etc/bristolbusbot`, readable only by the intended service identity,
  written atomically and never logged. Threads and Instagram credentials are
  separate.
- Public privacy, terms and data-deletion pages for Meta app review.
- Separate feature flags: Threads, Instagram draft generation, Instagram
  publishing.

### Insights

Collect platform insights 24 hours and 7 days after publication: IG
shares/reach, saves/reach, follows and profile activity per reach,
non-follower reach, Reel watch time/completion; Threads repost/reply rate,
profile visits, follower growth; duplicate rate, text parity, hourly-cadence
compliance. Missing/unavailable metrics remain `null` with an error/reason and
collection timestamp; they are never coerced to zero.

## 4. Rollout, testing and acceptance

### Rollout

1. **Account preparation**: retain existing accounts/handles; check IG
   recommendation eligibility; standardise profile image, bio, independence
   disclaimer, site link; three pinned posts ("Start here", "How the data
   works", latest weekly report); Highlights for Start, Weekly, Routes,
   Fleet, Good News, Method.
2. **Seven-day shadow period**: generate IG drafts without publishing;
   verify statistics, crops, credits, captions, mobile legibility; run
   Threads selection logging-only to inspect volume and quality. Shadow output
   includes the exact decision inputs/reason and proves a stopped `bbb-social`
   leaves the bot/collector/site/audit unaffected.
3. **Thirty-day pilot**: gated hourly Threads publishing; four manually
   approved IG feed posts/week; two manually assembled Trial Reels; Stories
   and all replies manual; record every factual correction, editorial edit,
   credit issue and publishing failure.
4. **Controlled automation**: consider auto-publishing only "Bristol Bus
   Week" and "Something Went Right", each requiring three error-free
   examples, zero factual corrections/licence failures/duplicates, and
   median share-plus-save ≥ account-wide median. Route features, Flickr
   posts, humorous posts, Stories and Reels remain reviewed.

### Required tests

- Threads uses the exact Bluesky text; no additional Gemini or
  transport-data request.
- Rolling-hour, overnight, same-route cooldown, DST and restart behaviour.
- Stable idempotency across crashes, retries and ambiguous API timeouts.
- Unknown delivery reconciliation proves no blind retry or duplicate post.
- One platform failing does not affect another.
- Killing `bbb-social` under load leaves collector, site, audit and Bluesky
  behaviour/cadence unchanged; recovery consumes only safe queued work.
- OAuth state replay rejection, token refresh, secret-log redaction.
- IG single-image and carousel container flows using API mocks.
- Rendering: dimensions, long text, missing fields, HTML escaping, golden
  images.
- Missing or insufficient audit data skips publication.
- Weekly/monthly aggregation sums counts/histograms; it never averages daily
  percentages or medians. Stale/frequent-service caveats render correctly.
- Flickr licence allowlist, last-minute licence recheck, attribution,
  exact API notice, ≤7-day candidate cache, archived permission, takedown
  mapping and end-to-end removal within 24 hours; malicious titles/names
  cannot inject markup.
- Draft media not publicly exposed; temporary public media resists path
  traversal and expires correctly.
- ARM64 `sharp` smoke test on the Pi.
- Slack approval: non-owner button presses rejected; double-press and
  wrong-team/wrong-channel requests rejected; double-press and Slack-then-
  dashboard races are idempotent; an edit invalidates approval; a stale Slack
  card cannot approve a newer revision; approval is recorded in the draft,
  not only Slack; current external upload flow works; dashboard approval works
  with Slack entirely unavailable; Slack tokens never log.
- Correction/retraction propagates to the mirrored Threads post and records
  what happened on each platform.
- Meta token refresh and expiry alerts at 14/7/2 days; expired media-container
  recreation; absent insights stored as null rather than zero.
- End-to-end dry run proving no change to collector consumption or Bluesky
  cadence.

### Acceptance criteria

- Zero duplicate social posts.
- Every Threads post exactly matches its source Bluesky text.
- Every Threads delivery has a successfully published source Bluesky URI and
  no low-confidence event is eligible.
- No Threads post exceeds the hourly or route cooldown.
- No rendered statistic differs from its underlying audit record.
- No Instagram post publishes without approval during the first 30 days.
- Every Flickr post has complete, clickable attribution.
- Every Flickr removal request is surfaced immediately and completed within
  24 hours with a durable audit record.
- Disabling either integration has no effect on the website, collector,
  audit pipeline, Bluesky or other services.
- Terminating `bbb-social` has the same non-effect on core services.
- Existing project tests continue to pass.

## 5. Design constraints

- "Once per hour" means a maximum of 24 Threads posts/day, not a requirement
  to fill every hour.
- Threads shares existing Bluesky content generation but performs its own
  Meta publishing request only after Bluesky succeeds.
- Existing Instagram and Threads profiles are retained.
- Main-feed humorous posts use native quote cards, not raw screenshots.
- Flickr is optional, human-reviewed, roughly fortnightly.
- Sunday is always constructive, alternating positive Fleet and City with
  Something Went Right; a slot is skipped when no valid story exists.
- The conservative Flickr licence policy assumes possible later monetisation.
- Automated video generation and automated comment replies are outside the
  first release.
- SIRI-SX disruption posts remain disabled until the collector mapping and
  event contract carry verifiable source/corroboration fields end to end.

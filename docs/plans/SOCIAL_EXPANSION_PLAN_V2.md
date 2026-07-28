# Social expansion, second edition

Status: accepted direction. The first four quick wins are implemented locally
and awaiting review; no Meta publisher or Pi social service has been deployed.
This is a reconsideration of `SOCIAL_EXPANSION_PLAN.md` (July 2026) against the
system as actually built, not as it was when that plan was written. The first
plan is retained for reference; where this document is silent, the first
plan's editorial and data safeguards still apply — they were its best part
and are kept nearly verbatim.

## 1. What changed since the first plan

The first plan was written before the shared collector existed. Several of
its assumptions are now obsolete, and one thing it never imagined is now
possible:

- **One data spine.** The collector matches vehicles, computes delays and
  writes corroborated `events` rows with a real primary key (`events.id`),
  a `vehicle_ref` that is identical everywhere, and the journey identity
  (`journey_ref` + `origin_aimed_departure`). The bot consumes those rows;
  the site's `/api/buses` serves the same `vehicleRef` for every live
  vehicle. The old "no clear primary key to say which bus at what time"
  problem — the reason post-to-map linking failed before — no longer
  exists. It died on 13 July 2026 when the collector went live.
- **The bot already records what it posted about.** On every successful
  Bluesky post, `engagement_analytics` stores the post text, significance,
  `vehicle_ref` and the AT-URI. Half the plumbing for a "posted-about
  buses on the map" feature already runs in production.
- **Vehicle profile pages exist** behind stable opaque slugs, with
  signed-delay histograms, and `/api/buses` already links live vehicles to
  them via `slug_for_vehicle(operator, vehicle_ref)`.
- **A proven human-approval pattern exists.** Editorial facts/news are
  discovered automatically, opened as a GitHub PR, and a merge is the
  approval; the Pi validates, promotes and health-gates delivery. The
  first plan proposed building a Slack Socket Mode approval app because
  none of this existed yet.
- **Credential-expiry alerting exists** (aggregate health already warns
  about the GitHub Actions token). The Meta token lifecycle can reuse it.
- **Timetable automation is done**, so the "finish the data estate first"
  gate has partially cleared, and the rest of it (Phases B–F) can be
  reassessed per feature instead of as a monolithic prerequisite.

## 2. Verdict on the first plan

| First-plan element | Verdict | Why |
|---|---|---|
| Isolation: separate `bbb-social`, own `social.db`, killing it harms nothing | **Keep** | Matches every project invariant; still right |
| Threads reuses exact final Bluesky text, no second Gemini/BODS call | **Keep** | Cheap, consistent, and the factual verifier's guarantees carry over for free |
| Idempotency on `(sourceBlueskyUri, platform)`; `unknown` state reconciled before retry | **Keep** | Correct engineering for a Meta API |
| Editorial/data safeguards (no invented causes, DfT definitions, counts-not-averaged-percentages, 48 h staleness, sample gates, alt text) | **Keep verbatim** | The strongest section of the first plan |
| Corrections workflow: source Bluesky deleted → Threads mirror deleted | **Keep** | Non-negotiable honesty |
| Threads capped at one post per rolling hour | **Replace** | Right instinct, wrong mechanism — see §4 |
| Instagram as four fixed weekly slots + 3–5 Story sets + 2 Reels/month | **Slim down** | A part-time job for one person; see §5 |
| Instagram publishing via Meta API from day one | **Defer** | Pilot the *content* manually before building the plumbing; see §5 |
| Slack Socket Mode interactive approval app | **Drop** | Reuse the GitHub-merge approval pattern when automation arrives; plain Slack webhook notifications suffice meanwhile |
| Flickr photo pipeline (licence allowlist, takedown workflow, credits pages) | **Defer wholesale** | Per-post licensing overhead is enormous for a solo maintainer; native cards and project-held route shapes suffice for a pilot |
| Automated insights collection at 24 h/7 d | **Slim down** | Read platform Insights by hand monthly during the pilot; automate only if decisions ever hinge on it |
| Monolithic gate: all of Phases B–F before any social work | **Replace** | Per-feature gates; see §7 |
| Post-to-map linkage | **Absent from first plan** | Now feasible and the best value-for-effort item on the whole list; see §3 |

## 3. Feature: posted-about buses on the live map

The new headline feature, and deliberately listed first: it is the smallest
useful, lowest-risk experiment available. Pure code on existing
infrastructure — no Meta app review, no tokens, no licensing, no editorial
risk, and it makes both existing products (site and bot) better rather
than adding a third mouth to feed.

### Why it works now

The old attempt failed because the old bot ingested SIRI directly and had
its own private view of vehicles — there was no shared key to say *this*
post is about *that* dot on the map. Now the chain is closed by
construction:

```
collector events.id ── vehicle_ref / journey_ref / origin_aimed_departure
        │ (bot consumes)
        ▼
bot posts to Bluesky ── engagement_analytics: vehicle_ref + post URI
        │ (new: exact provenance in app_data.db)
        ▼
site /api/buses ── already serves vehicleRef per live vehicle
```

`vehicle_ref` is the same string in all three places because there is only
one poller. The operator-safe identity work in Phase B concerns *fleet
enrichment* keys (registration vs fleet code); it does not block this
feature, because the map linkage never leaves collector-space.

### Design

- **Bot side.** At post time, alongside the existing analytics row, record
  the event's `id`, `journey_ref`, `origin_aimed_departure`, operator, line,
  delay, timestamp, final text and Bluesky URI in bot-owned `app_data.db`.
  A bounded loopback-only endpoint exposes fresh successful posts. There is
  no extra JSON writer, no change to `live.db`, and no writer to a
  collector-owned table.
- **Site side.** The site asks the loopback endpoint for a bounded recent
  history and decorates a vehicle only when operator, vehicle, journey and
  origin departure all match exactly. The map badges the vehicle; the
  sidebar shows the quoted post, the time it was posted, and a link to it on
  Bluesky. The endpoint URL is accepted only when it is loopback.
- **The honesty rule.** The badge means "the bot posted about this bus's
  current journey at 13:42", never "this bus is currently X late". The
  displayed quote carries its own timestamp, and the badge disappears when
  the journey ends or the entry ages out (suggest 90 minutes), because the
  claim in the post is about a moment, not a state. Same-journey matching
  is what makes this safe: once the bus is doing different work, the post
  no longer describes it.
- **Vehicle profile pages** get the durable version: a "the bot has
  mentioned this bus" section, built by joining `engagement_analytics` on
  `(operator, vehicle_ref)` through the existing `slug_for_vehicle`
  mapping during the nightly integration snapshot. The live map shows the
  last 90 minutes; the profile shows the history.
- **Reverse direction (later, optional).** Posts could occasionally carry
  a link facet to the vehicle's profile or the live map. Out of scope for
  the first cut: the persona's character budget is tight and the loop
  works without it.

### Acceptance

- A badge never appears on a vehicle whose current journey differs from
  the posted journey.
- Disabling the feature or losing the bot's loopback endpoint degrades to a
  map with no badges — never an error, never a stale badge.
- The site remains a read-only consumer; the bot never writes anything a
  release depends on.

## 4. Threads

### The volume question, answered properly

The first plan capped Threads at one post per rolling hour without saying
why, which made the number look arbitrary. The reasoning it should have
given:

- **Bluesky's following feed is chronological and opt-in.** People who
  follow the bot chose a live ticker; ~50–70 posts/day *is the product*,
  and each post costs followers nothing they didn't ask for.
- **Threads' default feed is algorithmic.** Most reach comes from
  distribution to non-followers, and the algorithm allocates it on
  per-post engagement. Seventy near-identical "the 75 is twelve minutes
  late" posts a day would earn terrible per-post engagement, and the
  account would learn to reach nobody. On Threads, volume actively
  cannibalises reach; on Bluesky it doesn't.
- **The API is not the constraint.** Threads allows 250 API-published
  posts per 24 hours — more than the full Bluesky firehose. Any cap is an
  editorial choice, so it should be an *informed* editorial choice, not a
  clock rule.

So the first plan's instinct (curate) was right and its mechanism (the
clock) was wrong. One-per-rolling-hour shapes output around the clock
rather than around the content: bus news clusters at the peaks, and a
rolling-hour rule both starves the morning peak and invites mediocre
mid-afternoon posts merely because the hour is empty (the plan patched
that with "never fill an empty hour", a rule needed only because the
clock rule created the problem).

### Replacement rules

- Mirror only, exact final Bluesky text, enqueued only after Bluesky
  returns a URI. Unchanged from the first plan.
- Selection is by **significance budget, not clock**: eligible posts are
  ranked by the significance score the bot already computes, and a daily
  budget of roughly 5–10 published mirrors is spent wherever the good
  material actually is — which will usually mean clustered at the peaks,
  and that is fine. Hard ceiling 15/day. No minimum: a boring day
  publishes nothing.
- Keep from the first plan: exclude low-confidence events; do not repeat
  the same operator+route within three hours; overnight (00:00–05:59
  Europe/London) only genuinely severe events; candidates expire (30–60
  minutes is enough — the event was fresh when Bluesky posted it);
  replies stay manual; one pinned introduction post.
- **The thresholds are decided by data, not by this document.** Run the
  selector in logging-only shadow mode for at least one complete service
  day and 50 decisions, look at what each candidate threshold would have
  published, and pick the one that yields a feed a Bristol person would
  actually follow. This is an evidence gate, not a calendar wait: continue
  as soon as the sample exists and the isolation test passes.
  Revisit after 30 days of real publishing using the account's Insights.
  If the evidence says Threads tolerates more volume, raise the budget —
  nothing in the architecture cares.

### Implementation shape

Unchanged in spirit from the first plan and slimmed in scope: a small
separate `bbb-social` process owning `social.db`, best-effort handoff
after Bluesky success, delivery record unique on
`(sourceBlueskyUri, platform)`, `unknown` timeouts reconciled against the
platform before any retry, concurrency one. Killing it must leave
collector, site, audit and Bluesky untouched — prove that in shadow. The
Meta token (60-day life) plugs into the existing aggregate-health expiry
warnings the same way the GitHub credential already does.

## 5. Instagram

### What the posts actually are

Instagram needs the most honest rethink, because it is the only platform
where content must be *made* rather than mirrored, and the maker is one
person. The first plan's four-pillar week was editorially sound and
operationally fantasy: four fixed slots plus three-to-five Story sets
plus two Reels a month is a content job, indefinitely, on top of running
the whole estate.

Rank the formats by what each uniquely offers and what it costs:

1. **The Bot Said** — the flagship, not the Friday filler. A full-bleed
   departure-board card of an already-published, already-fact-checked
   Bluesky post, with route, stop, time and measured delay on the same
   image. A compact receipt strip shows up to 20 real recent audit
   observations at that stop and marks the posted observation; when that
   history is unavailable it shows only the current observation, never
   invented comparison dots.
   This is the only format with genuine shareability (local humour +
   receipts), it carries zero new factual risk because the verifier
   already passed it, and it costs minutes. Runs whenever there is a strong
   candidate, normally once or twice a week; there is no quota.
2. **Weekly carousel** — the credibility anchor. Three fixed slides tell
   one story: B makes the headline physical as 100 squares; C shows the
   seven daily values on an explicitly zoomed axis plus a full-scale bar;
   D shows the signed delay distribution, median and p10–p90 spread.
   All numbers are deterministic from the audit rollups and distance-gated
   audit observations;
   the histogram must reproduce the published reading and on-time counts
   exactly or generation fails. Existing
   data gates from the first plan apply (all seven daily rollups,
   ≥1,000 in-gate observations, else skip). Weekly.
3. **Route Under the Microscope** — the depth format. Keep, but
   fortnightly, and only when its gates pass (≥4 service days, ≥200
   in-gate readings, ≥50 observed trips). No route league tables — the
   first plan was right about that.
4. **Something Went Right** — keep the *rule*, drop the fixed Sunday
   slot: at least one post in four must be constructive, enforced by the
   draft generator's bookkeeping rather than the calendar.

Pilot cadence: **regular Bot Said cards plus one weekly B→C→D carousel**,
with a third format at most when Microscope or Something Went Right has a
genuine story. Stories are reflex-only (resharing feed posts); no Reels
quota; no automated replies; comments checked on posting days. Every slot
is skippable — a skipped slot is the system working.

### Manual first: pilot the content, not the plumbing

The first plan coupled the editorial experiment to the full Meta
publishing stack: app review, OAuth, token files, public temp media URLs,
container lifecycles, a draft state machine and an approval UI. That is
months of plumbing in service of a question that can be answered in an
afternoon a week: *does anyone in Bristol want this account?*

Pilot shape:

- The draft-pack tool generates rendered 1080×1350 cards
  (SVG templates in the site's road-sign/matte/LED visual language,
  rendered with `resvg` and `sharp` — ARM64 smoke test still applies), captions, alt
  text, and the data-gate results that prove every number.
- During laptop testing, the maintainer can upload the files to the existing
  Slack DM. An unattended Pi job would need a narrowly scoped Slack app token with
  `files:write`; an incoming webhook can notify but cannot upload files.
  Generated packs and tokens stay out of Git.
- **The maintainer posts them natively from the phone.** Posting is the approval —
  no Slack buttons, no state machine, no Meta app, no public media URLs,
  no token lifecycle. The native app also permits formats the API makes
  painful (Stories, polls) at zero engineering cost.
- Numbers stay deterministic; Gemini may offer one optional humour hook
  for a caption and may never generate or alter a figure. All first-plan
  editorial safeguards apply to every card.

Start posting as soon as a reviewed draft pack is ready. Review the pilot after
eight weeks; this is an evaluation window, not a wait before launch. If the account resonates (steady saves/shares,
follower growth, anything organic), *then* build the publishing
automation — and when that day comes, approval reuses the editorial
pattern the project already trusts: drafts land as a GitHub PR, merge is
the approval, the Pi validates and publishes, with the same
idempotency/unknown-state discipline as Threads. If the account doesn't
resonate, the project has lost some render templates and learned
something, instead of having lost a Meta app review and a token
management subsystem.

## 6. What is deliberately cut

- **Flickr**, entirely, from the pilot. The first plan's licensing
  machinery (allowlists, evidence archiving, 24-hour takedowns, credits
  pages) was proportionate to the risk but not to the team size. Native
  cards and the project's own map renders carry the pilot. Revisit only
  if the account earns it.
- **The Slack interactive approval app.** Replaced by manual-native
  posting now and GitHub-merge approval later.
- **Reels quotas and Story schedules.** Reflex-only.
- **Automated insights collection.** Monthly manual read during pilot.
- **The `SocialPostEnvelope`'s Instagram half** until automation is
  actually built; the envelope ships with what Threads needs.

## 7. Sequencing: per-feature gates, not one big gate

The roadmap currently defers all social work behind Phases B–F of
`DATA_REFRESH_AUTOMATION.md`. Checked against what each feature actually
touches, that gate is broader than the risk:

| Feature | Genuine dependency | Blocked by B–F? |
|---|---|---|
| Post-to-map linkage | Collector-consistent `vehicle_ref` (already true by construction) | No |
| Threads mirror | Final Bluesky text + URI (already exists) | No |
| Instagram weekly carousel / Microscope cards | Published audit rollups (in production since launch) | No |
| Instagram fleet/vehicle features | Operator-safe identity | **Yes — Phase B** |
| Any AI caption garnish at scale | Phase F discipline | Yes, and it's optional anyway |

Phases B–F remain the priority *engineering* track and nothing here
argues otherwise. But the map linkage and the manual Instagram pilot are
small, independent, and touch none of the data-estate risk; they can
interleave with that work rather than queue behind all of it. The one
firm rule kept from the old sequencing: nothing social may ever add a
BODS consumer, a collector write path, or load on the audit pipeline.

## 8. Rollout

1. **Map linkage** (smallest useful experiment): bot records journey
   identity in `app_data.db` and exposes a bounded loopback recent-posts API;
   site badges same-journey
   vehicles; profile pages gain the mentions section. Ship behind a site
   feature flag; turning it off removes badges and nothing else.
2. **Threads shadow** (sample gate): selector logs at least 50 decisions
   across one complete service day; tune the significance budget on that
   evidence and prove a dead `bbb-social` affects nothing.
3. **Threads live** (30-day pilot): budgeted publishing, manual replies,
   pinned intro post. Monthly Insights read; adjust budget.
4. **Instagram manual pilot** (start immediately; assess at eight weeks):
   regular Bot Said cards, one weekly B→C→D carousel, native posting, plus
   an optional third format when there is a genuine story.
5. **Decision point**: continue, adjust, or stop Instagram; only on
   "continue" invest in Meta publishing automation with GitHub-merge
   approval, then consider auto-publishing the weekly carousel after at least
   three clean reviewed examples.

## 9. Success and kill criteria

Success is judged per platform, in plain terms: Threads — a feed a
Bristol person would follow, zero duplicate posts, zero mirrors of
deleted sources surviving. Instagram — organic saves/shares on at least
half the posts by week eight, zero factual corrections. Map linkage —
badges only ever on the right journey, and at least occasional evidence
(replies, clicks) that people follow a post to the map or back.

Kill without sentiment: Instagram, if week eight shows no organic
traction or any factual correction traces to a generated card; Threads,
if curation cannot produce a feed distinguishable from spam; the map
badge, if same-journey matching ever proves unreliable in practice. The
first plan's core acceptance list (exact text parity, no unapproved
publish, disabling any integration leaves everything else healthy,
existing tests pass) stands unchanged.

## 10. Numbers at a glance

| Platform | Volume | Mechanism |
|---|---|---|
| Bluesky | ~50–70/day (unchanged) | The firehose; its followers chose it |
| Threads | ~5–10/day, ceiling 15, no minimum | Significance budget tuned in shadow; API ceiling (250/day) is nowhere near binding |
| Instagram | Regular Bot Said + 1 weekly B→C→D carousel | Manual native posting from generated draft packs |
| Map badges | Every qualifying bot post | Same-journey match, 90-minute window |

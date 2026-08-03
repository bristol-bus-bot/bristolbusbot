# Enrichment automation, status-filter map UI and Slack-driven Instagram curation

Status: detailed design record, 31 July 2026, reviewed 2 August 2026.
Execution status and authoritative work order now live in
`ENRICHMENT_UI_SOCIAL_EXECUTION_INDEX.md`; this document is not one giant
implementation ticket.

This plan covers three pieces of work plus a sequencing section, in one
document because they share infrastructure and must land in a deliberate
order:

1. **Automate the enrichment data estate** — fleet/livery data, bus
   descriptions and all three AI blurb variants refresh themselves when new
   vehicles appear, the way stop data already refreshes itself inside the
   timetable pipeline.
2. **Clickable header status chips as map filters** — late / early / on time /
   at origin / at depot become filters that highlight matching buses using the
   existing route-view shading aesthetic.
3. **Slack-driven Instagram curation** — share a Bluesky link into a private
   Slack channel from the phone; the Pi verifies it, renders the Bot Said
   card with real audit receipts, and delivers the finished JPEG, alt text
   and caption back to Slack. Posting to Instagram stays manual.
4. **The wider social expansion**, resequenced so the curation flow lands
   first and `SOCIAL_EXPANSION_PLAN_V2.md` remains the authority for the
   rest (Threads shadow → Threads live → pilot decision → only then Meta
   publishing automation).
5. **Ongoing measurement-quality analysis** — the audit starts auditing
   itself. Anomalous readings (a bus 68 minutes late, a bus shown heading
   the wrong way) get detected, evidence-captured, investigated,
   classified, and turned into regression fixtures and matcher
   improvements, on a standing basis rather than when someone happens to
   notice on the website.
6. **Addendum: the long-term Rust consolidation** — a corrected and
   re-grounded version of an earlier migration plan, gated behind
   everything above.

Before any of it: **Part 0**, a live production defect. The editorial news
discovery workflow has failed on every scheduled run since 1 August. It is
diagnosed below and fixing it precedes everything else in this document.

Part 1 concretises Phases B–F of `DATA_REFRESH_AUTOMATION.md` for the
enrichment estate; that document's principles (fail closed,
candidate/validate/promote, old valid data beats new doubtful data) are taken
as binding and are not restated in full here. A per-locality blurb idea was
considered and dropped: one more text layer made the vehicle card too busy
for its value, and place-based humour carried the highest review burden of
anything in the plan.

## Ground truth verified before planning

These are the facts the plan is built on, checked against the repository on
31 July 2026:

- Enrichment today is a **manual workstation loop**: `pipeline/refresh_enrichment.py
  --fix` fetches fleet data from bustimes.org, builds a blurb scope from the
  collector's `live.db`/`audit.db`, runs the three Gemini generators
  incrementally (only missing fleet codes), then `distribute()` copies the JSON
  files into `site/` and `bot/data/`. Production receives them **inside code
  releases** via `deploy/push.py`. If the laptop stays off, this data silently
  ages — exactly the situation the timetable automation was built to end.
- The three generator scripts each carry their own **duplicated, hard-coded
  `MODEL_BLURBS` dict**. A genuinely new bus model gets no model context in any
  generator until someone edits three files by hand.
- `build_blurb_scope()` still collapses vehicle refs with
  `ref.split("-")[-1]`, and the data estate has ~71 duplicated active
  fleet-code groups across operators (Phase B's known landmine). A bare fleet
  code is not a safe global key.
- `update_fleet_data.py` breaks out of an operator's pagination loop on the
  first request error and carries on — a partial fetch can masquerade as a
  successful run. The only downstream guard is `distribute()` refusing a
  greater-than-half shrink.
- The site reads every enrichment file through environment-overridable paths
  (`site/app/config.py`); the bot still reads `stop_localities.json`,
  `local_flavour.json` and friends from compiled-relative paths
  (`bot/src/services/ai-commentary.ts`).
- The site caches `/api/bus-descriptions` for the **process lifetime**
  (`_cache` in `site/app/routes/api_misc.py`), so any data promotion must
  restart the site — which the editorial promotion pattern already does.
- The proven template for safe Pi-side data delivery already exists:
  `bbb-editorial-fetch.service` / `bbb-editorial-promote.service` (unprivileged
  fetch + validation, separate root promoter, `.previous` copy, atomic replace,
  restart, health gate confirming the promoted SHA-256, rollback).
- Frontend: header chips are plain `<div>`s in `templates/index.html`
  (`count-punctual`, `count-early`, `count-delayed`, `count-waiting`,
  `count-depot`). Counts are computed inline in `updateBusMarkers()` in
  `app.js`. Marker appearance is centralised in `markerVisual(bus)`, which
  already supports a de-emphasis mode: in route view, non-matching buses are
  drawn **hollow** (`options.hollow` → grey outline shapes in
  `map_render.js`). Depot markers return early from `markerVisual` and have no
  hollow variant.
- `/api/buses` swaps the raw stop code for a display name before responding
  (`api_buses.py`), so the frontend currently has **no stop code and no
  locality** for a moving bus.
- Blurbs are keyed by `fleet_code` only, in three state-variant sets;
  `pickDescriptionFor()` in `app.js` selects waiting → depot → in-service.
- `stop_localities.json` maps stop_code → ward name/code + area;
  `local_flavour.json` is curated neighbourhood editorial used only by the bot.
- The Pi: ~904 MiB RAM, four live services, eleven timers, one maintenance
  lock, and now a large SSD. Disk space is no longer a constraint; RAM and
  scheduling contention still are. The remaining Phase A gate (service-window
  validator + correlated monitoring, from the 29 July incident) is implemented
  in source but not yet proven on a promotion-disabled shadow.

## What "automatic like the stop data" actually means

Stop data is automatic because it travels **inside the timetable artifact**
through a full contract: detection (Pi timer), generation (GitHub), independent
validation, atomic promotion, health gate, rollback. Nothing about the current
enrichment scripts has that contract. So the job here is not "put
`refresh_enrichment.py --fix` on a timer" — scheduled as-is, a partial
bustimes fetch or an unfenced generator run could silently degrade production
data. The job is to give the enrichment estate the same
candidate/validate/promote shape the timetable already has, then schedule it.

**Plain English:** the timetable updates safely because every update is built
as a *candidate*, checked hard, swapped in atomically, and swapped back out if
anything looks wrong. The enrichment files need that same safety harness
before we let them update themselves.

## Architecture decision: enrichment generation runs on the Pi

The timetable runs on GitHub because it is heavy (RAM/CPU) and built from
redistributable open data. Enrichment is the opposite on both axes:

- The fleet file comes from bustimes.org and **must not pass through GitHub**
  (already policy: it is not redistributed in the repository).
- The blurb scope fence needs the collector's `live.db` and `audit.db`, which
  exist only on the Pi.
- The work is network-and-JSON light: a fleet fetch, a handful of Gemini API
  calls. Well within 904 MiB alongside the live services, and the new SSD
  comfortably absorbs staging copies, `.previous` rollback files and pending
  blurb batches.

So: **Pi detects, Pi generates, Pi validates, Pi promotes**, reusing the
editorial fetch/promote split (unprivileged generator, root promoter). GitHub
is not involved except that blurb files, which are public today, remain
committed to the repository on a follow-up commit for provenance.

One new secret lands on the Pi: `GEMINI_API_KEY` in `/etc/bristolbusbot`
(mode 0600, never logged). Per-run and monthly cost ceilings live in
configuration, not in code.

---

# Part 0 — Priority zero: the failing "Propose official bus news" workflow

## Symptom

`editorial-news.yml` (cron `23 */6 * * *`) has failed on **every scheduled
run since 1 August**, each time in 8–11 seconds, one annotation per run.
Before that it had been green for weeks.

## Diagnosis (evidence gathered 2 August, reproduced locally)

The timeline is the tell. At **23:01 UTC on 31 July** the DfT published
*"Summer holiday savings start now with free buses for kids"* — a
`press_release` under `/government/news/` containing the word "buses":
the first story in the workflow's life that passes every discovery
filter. The failures began on the next scheduled run and have recurred on
every run since.

What was verified by reproduction (not guessed):

- **The GOV.UK search API is healthy** — fetched live on 2 August; the
  story is the top result.
- **Discovery works.** `discover_editorial_news.py` run locally against
  the live API payload selects the story, builds its requirements
  checklist, updates a scratch copy of `editorial-context.json` and exits
  0.
- **Validation works.** `deploy/editorial_context.py` accepts the
  resulting file (2 news items, exit 0).
- **The found-a-story tail has never run in production.** No commit
  matching the workflow's `"Propose approved news context: …"` message
  exists in the history of `bot/data/editorial-context.json`. Every green
  run before 1 August took the exit-75 "nothing found" path. The one
  existing news item was committed by hand.

So this is the first-ever exercise of the workflow's untested tail, and
it fails there — a latent defect that waited for its first real input.
Because the PR is never created, the story is never recorded as reviewed,
so **every run retries the same story and fails the same way**: a poison
loop that will alert every six hours until the story ages out of the
7-day window at 23:01 UTC on 7 August. If the alerts stop then, that is
the loop expiring, **not** the defect being fixed — the next qualifying
DfT bus story re-breaks it.

## Confirmed cause and remaining hardening

Verified from run `30763871340` on 2 August: discovery, validation, duplicate
checking and `git push` succeeded. `gh pr create` failed with GitHub's explicit
message that Actions was not permitted to create or approve pull requests. The
repository workflow permission was disabled. The eight failed runs also left
eight run-ID-suffixed remote branches; they were verified as unattached to any
PR and removed on 2 August.

The original suspects are retained below as incident history. Suspect 1 is the
confirmed cause; suspects 2 and 3 were exonerated by the failing log.

## The three original suspects

Everything before and including validation is exonerated above. The
remaining steps, with fix per branch:

1. **`gh pr create` refused** — likeliest. Repository/organisation
   setting "Allow GitHub Actions to create and approve pull requests"
   must be enabled; the workflow's `pull-requests: write` permissions
   block is *not* sufficient on its own, and this failure mode only
   appears the first time creation is attempted. Fix: Settings → Actions
   → General → Workflow permissions → enable the toggle. No code change.
2. **The duplicate-check search** — the sneaky one.
   `gh pr list --search "editorial-source:${SOURCE_ID} in:body"` sends
   `editorial-source:` as an unknown search *qualifier*, which GitHub's
   search can reject as invalid rather than treat as text. Fix: quote it
   as a phrase (`--search "\"editorial-source:${SOURCE_ID}\" in:body"`)
   or, better, drop the search-qualifier approach and reuse the exact
   `--json body` + capture pattern the "already reviewed" step already
   uses — one mechanism instead of two.
3. **`git push` of the automation branch refused** — possible if a
   ruleset/branch protection restricts branch creation. Fix: permit the
   token to create `automation/*` branches.

## Fix procedure

1. Work from a clean branch based on a freshly fetched remote `main`. Do not
   blanket-`pull` over a dirty or non-main worktree.
2. Open the failed run, read the single annotation, apply the matching
   fix above.
3. Re-run via `workflow_dispatch` (the trigger already exists). The
   attended run **is** the integration test the tail never had. Expect a
   PR proposing the summer-fares story; review it on its merits.
4. If the story has aged out before the fix lands, force one
   `workflow_dispatch` run anyway once a new qualifying story exists, or
   temporarily widen the window in a test invocation — do not declare
   victory on a run that found nothing.

## Hardening (same session, small)

- Make a PR-creation failure fail *loudly and distinctly*: echo the story
  title and failing step into the job summary so the email names the
  story, not just "propose failed".
- The poison-loop property is worth removing: if PR creation fails, the
  run should still surface the source id it attempted, so a human can
  exclude or fix deliberately rather than receiving four identical
  alerts a day.
- Feed the lesson into house practice (it is Part 5's lesson in workflow
  form): **a path that only executes on rare input gets an attended
  forced exercise at deployment time**, the way timetable promotion had
  its commissioning run. `workflow_dispatch` existed here; it was never
  used to rehearse the found=true path with a synthetic story.

**Plain English:** the news-scout has been reporting "nothing to see"
successfully for weeks. On 31 July it finally found a story worth
proposing — and the half of the machine that writes up the proposal had
never actually been switched on before. It jams, and because the write-up
never happens, the scout finds the same story every six hours and jams
again. The story it found is real and probably worth approving; the fix
is likely a one-toggle repository setting, and the test is simply
pressing the workflow's manual run button and watching it open the PR.

---

# Part 1 — Enrichment automation work packages

Ordering matters. WP0 and WP1 are prerequisites; skipping them means
automating the production of wrong data.

## WP0 — Finish the Phase A hardening gate first

The service-window validator and correlated monitoring corrections from the
29 July incident must pass their promotion-disabled Pi shadow and a fresh
attended promotion before any new data-plane automation starts. One change
stream on the data plane at a time.

**Plain English:** the timetable safety net is mid-upgrade. Finish stitching
that before adding new machinery that shares the same locks, timers and alert
channels — otherwise you can't tell whose alert is whose.

## WP1 — Vehicle identity (Phase B)

Adopt the identity model already specified in `DATA_REFRESH_AUTOMATION.md`:
registration is canonical where present; a source-stable ID is the fallback;
`(NOC, fleet_code)` is the operator-scoped lookup key; a bare fleet code is
never a global key.

1. Write a read-only audit script that lists every active fleet-code collision
   across operators and checks whether any collision currently produces a
   wrong description or livery on the site. This is evidence, not a fix.
2. Introduce dual-key reads in the site and bot fleet lookups: try
   `(NOC, fleet_code)` first, fall back to the legacy bare code, log the
   fallback.
3. Migrate the three blurb files and the fleet lookup to operator-scoped keys
   (e.g. `"FBRI:36284"`), with the legacy bare-code key accepted during a
   transition window.
4. Replace the `ref.split("-")[-1]` collapse in `build_blurb_scope()` with
   scoped identities, with test fixtures that include two operators sharing a
   fleet code.

**Plain English:** two different companies can both own a bus numbered 36284.
Today some code treats "36284" as one bus. Before blurbs are generated
automatically, every lookup needs to know *whose* 36284 it is, or the wrong
bus gets the wrong joke.

Acceptance: grep-backed inventory of every consumer read path; collision
fixtures pass; no fallback-log entries after the migration window.

## WP2 — Durable consumer paths (Phase B continued)

Add tested environment-variable overrides for every bot-consumed artifact
(fleet, localities, stop enrichment, local flavour, route details), mirroring
what `site/app/config.py` already does. No symlinks into immutable release
directories.

**Plain English:** the site can already be told "read your data from over
here" via settings. The bot mostly can't — it looks in its own installation
folder. Both need the setting so data can live in one durable place.

## WP3 — Data/code decoupling and a generic promotion helper (Phase C)

1. Create `/var/lib/bristolbusbot/enrichment/` (backed up, on the SSD).
2. Seed it from the current verified live release; point consumers at it one
   artifact at a time via the WP2/site env vars, restarting and health-checking
   after each.
3. Generalise the editorial promoter into a small **data promotion helper**:
   fixed staging path, artifact-specific validation hook, `.previous` copy,
   atomic replace, consumer restart, health gate, rollback, all under the
   maintenance lock. The editorial units prove this design already works on
   this host; this is a refactor-and-reuse, not an invention.
4. Once every consumer reads durable paths, stop packaging the mutable JSON
   files into code releases.

**Plain English:** today the data ships glued inside the website's code
releases. This step gives data its own home on the Pi, and one shared, tested
"swap the file safely" tool that every later job uses. Build the tool once,
trust it everywhere.

Acceptance: a code deploy leaves enrichment untouched; a data promotion
changes no code; a forced validation failure restores the previous file and
the site keeps serving the old data throughout.

## WP4 — Detection: the nightly data-health audit (Phase D)

A read-only nightly Pi job (report-only for its first two weeks) that answers,
among the Phase D questions, the ones this plan needs:

- Which observed, active, operator-scoped vehicles are missing a livery, a
  fleet entry, or any of the three blurb variants?
- Which `vehicle_type` names present in the fleet are **absent from the
  model-context file** (see WP6)? This is the "new bus model" detector.
- Is the fleet file old, or drifting from vehicles actually observed by the
  collector?
- Did the last timetable promotion introduce stops without locality data?

Output is one versioned JSON report through the existing `run_recorded_job.py`
→ `aggregate_health.py` → digest seam. Thresholds in one config block.

**Plain English:** every night, a job that changes nothing looks at what the
collector actually saw on the road and writes a short report: "3 new buses
have no livery data, 1 new model I've never heard of, fleet file is 12 days
old." The morning digest reads that report. For the first two weeks it only
reports, so you can check its counts by eye before anything acts on them.

(Scope note: WP4 asks "is the data estate *fresh and complete*?" Part 5's
measurement-quality job asks the different question "are the *numbers
inside it* plausible?" They are separate jobs feeding the same digest
seam.)

## WP5 — Fleet regenerator (Phase E)

Refactor `update_fleet_data.py` to the candidate contract before scheduling
it:

- explicit input/output paths, zero production writes; output is a staging
  candidate only;
- honest User-Agent, bounded retries and timeouts, keep the polite pacing;
- remove the catch-print-break: every configured operator must end in an
  explicit per-operator result (fetched N / source-failed), and a source
  failure anywhere discards the entire candidate;
- validation gates: JSON shape, per-operator active-count comparison against
  the previous file (bounded change), rejection of unexplained empty or
  collapsed operator results;
- promote the complete combined file through the WP3 helper; never through
  GitHub.

Schedule weekly plus on WP4 drift detection, spaced away from the timetable
shadow and backup timers, sharing the maintenance lock with a deadline and a
named refusal rather than a late overlapping run.

**Plain English:** the current fleet script shrugs and keeps going if one
operator's download breaks halfway, which could quietly delete half an
operator's buses (and their livery colours) from the map. The rewrite makes it
all-or-nothing: fetch everything cleanly or change nothing, and say which.

Acceptance: a mid-fetch injected network failure leaves the live file
untouched and produces a named failure in the digest; a normal run promotes
and both site and bot pick the file up after restart.

## WP6 — Blurb generation, gated (Phase F)

This is the piece that makes livery-less new buses get their blurbs without
the laptop. It only starts once WP1–WP5 are stable.

**Model context first.** Centralise the triplicated `MODEL_BLURBS` dict into
one versioned file, e.g. `pipeline/model-context.json`, read by all three
generators. It stays **human-curated**: when WP4 flags an unknown
`vehicle_type`, writing its two-line technical context is a 5-minute human
task surfaced in the digest. Vehicles whose model has no context entry are
**skipped by auto-generation** (absence of a blurb is cosmetic; a
context-free blurb is low-quality and permanent-feeling). This keeps the
quality bar without blocking the rest of the fleet.

**Generation job**, triggered when WP4 reports missing blurbs for observed
active vehicles:

- rebuild the blurb scope with WP1 identity-safe keys; refuse to run unfenced
  (today's generators run unfenced when the scope file is missing — that
  becomes a hard stop);
- run the three generators incrementally against the staged fleet candidate,
  with hard per-run and monthly cost ceilings from config;
- treat all bustimes-sourced fields (names, liveries, branding, notes) as
  untrusted prompt *data*: normalise, length-limit, and frame as data, never
  as instructions;
- deterministic output gates: valid JSON; keys are exactly the requested
  scoped codes and nothing more; length ceilings; no URLs, handles, HTML or
  emoji; profanity list; British spelling left alone; existing entries
  byte-identical (generation may add, never edit or remove);
- **whole-batch discard** on any single failure.

**Approval.** New blurbs land in a pending file, not production. A small
attended review command (run over SSH, 2 minutes) shows the pending entries
and promotes the approved set through the WP3 helper; the digest reports how
many are waiting. Hold this human window for at least the first 30 days /
several batches, per the master plan, then decide deliberately whether
auto-promotion after clean history is worth it. Promoted blurb files also get
committed back to the repository (they are public today) for provenance —
as a human `git` action, not a Pi credential.

**Plain English:** when new buses appear, the Pi writes their one-liners
itself, but they sit in a waiting room until you glance at them and say "yes,
post those". Every safety check is mechanical and boring on purpose; the only
creative act left to review is the joke itself. If any one blurb in a batch
fails a check, the whole batch is thrown away — a half-good batch is how weird
text sneaks onto the site.

Acceptance: adversarial fixtures (prompt-injection strings in livery/branding
fields, over-length outputs, extra keys, profanity) all cause whole-batch
discard; a month of pending batches reviewed; a failed run provably cannot
remove or alter any existing description; cost counters visible in the digest.

---

# Part 2 — Header status chips as map filters

Small, self-contained, frontend-only. It can ship **before or in parallel
with** Part 1 because it touches no data plane. It is a good first task.

## Behaviour

- Each of the five chips (on time, early, late, at origin, at depot) becomes a
  toggle button. Click once: the map de-emphasises every bus *not* in that
  state, exactly in the route-view aesthetic (hollow grey outline markers),
  and matching buses keep full colour and rise in z-order. Click again, or
  press Escape: filter clears.
- **Single-select to start** (clicking a second chip switches the filter to
  it). Multi-select is a plausible later enhancement but complicates the
  visual language for little value; start simple.
- Status filter, route view and vehicle selection are **mutually exclusive
  emphasis modes**: activating any one clears the others. Composing them
  ("late buses on the 75") is a follow-up decision, not v1 — mixed emphasis
  states are where this kind of UI quietly becomes unreadable.
- Counts keep updating live while filtered. A bus whose state changes simply
  restyles on the next poll, including falling out of or into the filter —
  no special handling needed because appearance is recomputed per update.

## Implementation steps

1. **Extract one classifier.** In `app.js` (or `util.js`), a pure
   `statusOf(bus)` returning `punctual | early | delayed | waiting | depot`,
   where `waiting` means `eventType === 'waiting' || bus.waitingAtOrigin` —
   the exact logic currently inlined in `updateBusMarkers()`'s count code.
   Both the counts and the filter must call this one function so they can
   never disagree. Add unit tests in `site/tests/js`.
2. **Header markup** (`site/templates/index.html`): convert the five
   `.stat-chip` divs to `<button>` elements with `aria-pressed`, keeping the
   existing ids and inner spans so the count-update code is untouched.
3. **State + wiring** (`app.js`): an `activeStatusFilter` module variable
   beside `activeRouteLine` / `activeRouteVehicleRef`; click handlers that
   toggle it, clear the other emphasis modes (reusing the existing route-view
   close path), and call `syncAllMarkerAppearances()`.
4. **Marker styling** (`app.js` `markerVisual`): when `activeStatusFilter` is
   set and neither route mode is active, set `options.hollow = statusOf(bus)
   !== activeStatusFilter`, z-offsets 1600/400 as in route mode, and include
   the filter in the icon cache `key` (this is what makes icons actually
   refresh — the route modes already show the pattern).
5. **Depot de-emphasis** (`map_render.js`): `markerVisual` returns early for
   depot buses, and `depotIcon` has no hollow variant. Add one (outline-only /
   reduced-opacity version), and route the depot early-return through the
   filter check so depot markers dim when filtering on a moving state and
   stay full when filtering "at depot".
6. **CSS** (`chrome.css` and `matte.css`, including the ~1485 mobile block in
   matte.css): pressed-state styling for the chips in both themes, visible
   focus ring, slightly larger tap targets on mobile. Follow the existing
   toggle-btn (`routes` button) pressed styling for consistency.
7. **Accessibility**: `aria-pressed` reflects state; Escape clears; each
   button gets an `aria-label` like "filter map to late buses".
8. **Tests**: classifier unit tests; a DOM test that toggling a chip flips
   `aria-pressed` and that the icon key changes for a non-matching bus.

**Plain English:** the map already knows how to fade every bus except the
ones you care about — that's what happens when you pick a route. This reuses
that exact mechanism, but the "buses you care about" become "buses in the
state whose chip you clicked". The one subtle trap is that markers cache
their drawn icon by a key string; if the filter isn't part of that key, the
map won't redraw when you toggle. Step 4 handles that.

Acceptance: filter on/off round-trips with no leftover hollow markers; counts
never disagree with the filter; depot chip works both as filter target and as
filtered-out state; keyboard-only operation works; mobile tap targets pass.

---

# Part 3 — Slack-driven Instagram curation ("share a link, get a card")

The phone experience: share a Bluesky link into a private Slack channel →
a minute or two later the finished 1080×1350 Bot Said card, alt text and a
copy-ready caption appear in the same channel → post it to Instagram
natively from the phone. Posting remains the approval, exactly as
`SOCIAL_EXPANSION_PLAN_V2.md` §5 decided; this feature automates the
*drafting*, never the publishing.

## Ground truth verified before planning

- The deterministic renderer exists (`social/generate-pack.mjs`, resvg +
  sharp) and produces the Bot Said quote card — but its pack validator
  currently **requires both `botSaid` and `busWeek`**, and `build_pack.py`
  requires `--audit-json` and `--recent-posts-json` together. A proper
  single-card mode is a real change, not a flag that already exists.
- The bot records exact successful-post provenance in `app_data.db`
  (final text, AT URI, vehicle/journey identity) and serves a bounded
  loopback `/api/recent-posts`. The architecture docs already sanction a
  **read-only social selector** on `app_data.db`, and `build_pack.py`
  already reads `audit.db` read-only for the receipt strip (up to 20 real
  observations at the posted stop; it never invents comparison dots).
- Slack today is an **incoming webhook for monitoring only** — it cannot
  upload files. `social/README.md` already names the minimal future setup
  this plan builds: a narrowly scoped Slack app token with `files:write`,
  root-readable, using Slack's external-upload flow
  (`files.getUploadURLExternal` → `completeUploadExternal`).
- V2 deliberately **dropped** the Slack Socket-Mode interactive approval
  app. This is not that: no buttons, no state machine, no approval
  semantics in Slack, and polling keeps the Pi outbound-only (no inbound
  webhook surface), consistent with the "nothing on the internet can reach
  the Pi" posture.
- Slack Free retains 90 days of history, so Slack is a **transport, not a
  record**: `social.db` is the ledger of what was requested, rendered and
  delivered.

## The flow

1. A Pi timer polls the one private channel (e.g. `#instagram-drafts`)
   every few minutes.
2. Accept a message only when it is from the maintainer's Slack user ID,
   in the allowlisted channel, and contains a `bsky.app` post link.
   Everything else gets a threaded "couldn't use this because…" reply.
3. Resolve the link to a post identity: a `bsky.app` URL carries the actor
   and record key (`rkey`). Resolve the actor to its DID, construct the full
   `at://DID/app.bsky.feed.post/RKEY` identity, and require an exact match in
   `app_data.db`. Never match on `rkey` alone. A link to anyone else's post —
   including quotes or reposts *of* the bot — is refused.
4. Verify provenance from `app_data.db`: the exact final text, route,
   stop, observed time and measured delay recorded at post time. **Slack
   supplies only a lookup key; no Slack-provided text is ever rendered
   onto a card.** The card can therefore never carry a doctored quote.
5. Pull the receipt-strip observations from `audit.db` read-only, exactly
   as `build_pack.py` does today.
6. Render the single card with the new single-card mode, auto-shrinking
   long quotes by stepping the font size down until the full text fits —
   never truncating — and refusing below a minimum legible size.
7. Upload the full-resolution JPEG via the external-upload flow, then post
   the alt text and a separate copy-ready caption as threaded messages.
   Captions are deterministic from provenance and audit facts; Gemini is
   not involved anywhere in this path.
8. Record the delivery in `social.db`, unique on (post URI, card kind,
   template version): the same link shared twice gets a threaded "already
   made this" pointing at the original delivery, not a duplicate card. An
   attended `--new-version` is required for intentional regeneration.

Separately, a periodic **shortlist message**: recent successful posts
ranked by likes + reposts, read from Bluesky's public AppView
(unauthenticated `app.bsky.feed.getPosts` on stored URIs). It is a
suggestion feed only — sharing any other link always wins. If the AppView
is unreachable the shortlist degrades to recency or is skipped; it never
blocks the main flow. This adds an outbound read to Bluesky's public API
from the social service; it adds no BODS consumer, no collector write and
no audit load, keeping V2 §7's one firm rule intact.

**Plain English:** you see a bot post that makes you laugh, you share it to
a private Slack channel from your phone, and the Pi does the checkable part:
proves the post is really the bot's, rebuilds the card from the same
databases the post came from, and hands you back the image and caption.
You still press "post" on Instagram yourself. The Pi never posts anywhere,
and nothing typed into Slack can end up on a card — only the link is used,
and only to look up what the bot already said.

## Work packages

### S1 — Renderer single-card mode

Split the pack validator so a bot-said-only pack is legal (`busWeek`
requirements apply only when weekly slides are requested), add the
quote-autofit behaviour, and add a `build_pack.py` mode that takes one post
identity instead of requiring the weekly audit JSON. Unit tests for the
validator split and autofit bounds, then the ARM64 smoke test on the Pi —
resvg and sharp ship native binaries and this is the step that proves they
run there.

### S2 — Curation service

A small Python orchestrator (matching `threads_candidates.py` and
`build_pack.py`, shelling out to the Node renderer exactly as the manual
flow does today): poll → verify → build pack → render → upload → record.
Concurrency one; every run writes a job record.

### S3 — Slack integration

A Slack app (reusing the existing BristolBusBot app if scopes allow) with
the narrowest workable grant: read + reply in the one private channel
(`groups:history`, `groups:read`, `chat:write`) plus `files:write` and
`files:read`. The read scope is used only to reconcile an uncertain upload
before a retry; without it the service fails closed instead of risking a
duplicate. The
token is stored root-only and passed via systemd `LoadCredential`, never in
the repo, releases, logs or chat. Uploads retry with bounded backoff;
before any retry the service reconciles against `social.db` and the Slack
file listing so an "unknown" outcome cannot become a duplicate — the same
unknown-state discipline V2 mandates for Threads.

### S4 — Isolation and deployment

A new `bbb-social-curation.service` + timer, sandboxed like every other
project oneshot: read-only access to `app_data.db` and `audit.db`, write
access only to `/var/lib/bristolbusbot/social/`, `ProtectSystem=strict`,
no access to the bot token, BODS key or Gemini key. It becomes a proper
deploy component in `push.py` with its own health gate and job records
feeding aggregate health and the digest. The acceptance test from V2
stands: kill the service and the collector, site, bot and tunnel are
provably untouched.

### S5 — Shortlist job

The engagement-ranked suggestion message, once S1–S4 are proven. Optional
and last for a reason: it is the only piece that adds a new external read,
and the feature works fine without it.

## Rollout (each step is an approval gate)

**Implementation evidence, 4 August 2026:** the renderer completed a native
ARM64 1080 x 1350 smoke render on the production Pi from the exact reviewed
source without contacting Slack. PR #32 was then merged and release
`20260803t234404745058z-4ab3bcea` plus the isolated systemd layout were
installed successfully. The timer is disabled and inactive, the service is
inactive, and no Slack credential or live marker exists. That is production
installation evidence, not delivery evidence: the service has not read a real
channel and no card has been uploaded. The unchecked steps below remain real
approval gates.

1. Build and test everything locally; no Slack contact.
2. Pi ARM64 render smoke test, still no Slack contact.
3. Maintainer configures the Slack app and token once, directly on the Pi
   (the token never transits chat or the repository).
4. Shadow mode: the service reads a test link and renders locally but
   sends nothing. Run until a handful of links have round-tripped cleanly.
5. Enable live mode, share the link again as a new Slack message, and make one
   attended real delivery; verify the phone-downloaded image, alt text and
   caption byte-for-byte against the local render. The checkpointed shadow
   request is never silently replayed.
6. Enable the timer.
7. Instagram posting stays entirely manual — that is a feature, not a gap,
   until the V2 §8 decision point says otherwise.

## Acceptance

- A link to any post not authored by the bot yields a refusal reply, never
  a card. Fixtures include: someone else's post, a quote-post of the bot,
  a deleted post, a malformed URL, a message from a non-allowlisted user.
- The same link twice yields one card and one "already made this" reply.
- No Slack-originated text appears in any rendered card or caption.
- The Slack token is unreadable by unprivileged users and absent from logs.
- A renderer or upload failure leaves no partial state: `social.db` and
  the channel agree after reconciliation.
- Killing `bbb-social-curation` mid-run leaves every other service healthy.

# Part 4 — The wider social expansion

`SOCIAL_EXPANSION_PLAN_V2.md` remains the authority; nothing here rewrites
its editorial rules, volume budgets, cut list (no Flickr, no Socket-Mode
approval app, no Reels quotas) or kill criteria. What changes is sequence:
the Slack curation flow is pulled forward because it upgrades the tooling
for V2's rollout step 5 (the Instagram manual pilot) while touching none of
the Meta-facing risk.

Revised social order:

1. **Map linkage** — done (V2 step 1, shipped).
2. **Instagram review tooling** — done (V2 step 4, exercised manually).
3. **Slack curation flow** — Part 3 above. Makes the manual pilot cheap
   enough to sustain: regular Bot Said cards on demand plus the weekly
   carousel per V2 §5 cadence.
4. **Instagram manual pilot proper** — start when ready, assess at eight
   weeks per V2 §9's success/kill criteria. The curation flow's `social.db`
   proves drafting and Slack delivery only; Instagram posting remains a
   separate manual confirmation.
5. **Threads shadow** — the logging-only selector runs for at least one
   complete service day and 50 decisions; thresholds chosen from evidence
   (V2 §4). The isolated `bbb-social` delivery service builds on the same
   isolation pattern S4 has by then already proven in production.
6. **Threads live** — 30-day budgeted pilot, manual replies, per V2.
7. **Decision point** — continue/adjust/stop per platform. Only a
   "continue" verdict funds Meta publishing automation, with GitHub-merge
   approval and the idempotency discipline V2 specifies.

The weekly carousel remains generated by the existing pack tooling on the
laptop or Pi and delivered through the same Slack channel once S3's upload
capability exists — one channel for all Instagram material, no second
integration.

**Plain English:** the order is deliberately "make the manual Instagram
routine effortless, prove people want it, then automate Threads, and only
build Meta publishing plumbing if the evidence says the accounts are worth
it." Every stage can be stopped without stranding work: the curation flow
is useful even if Threads never happens, and the isolation service pattern
it proves is exactly what Threads needs next.

---

# Part 5 — Ongoing measurement-quality analysis (the audit audits itself)

## The problem, precisely

Two real observed symptoms motivate this: a bus displayed 68 minutes late,
and a bus displayed heading the opposite direction to where it was
physically going. Both are *measurement* anomalies — the data estate can be
perfectly fresh (WP4's job) while the numbers inside it are wrong. Nothing
currently detects, preserves or explains such readings; they are found by a
human happening to look at the map.

Why they can happen is already documented in the system's own hard-won
knowledge: the SIRI `DatedVehicleJourneyRef` is just a start time, so
vehicle-to-trip matching is fuzzy by necessity; SIRI `DirectionRef` can be
wrong or absent; some operators publish only one direction of a route; GTFS
times past 24:00 need a day anchor; and superseded timetable editions can
overlap. A "68 minutes late" reading may be genuinely a bus 68 minutes late
(First provides these), or the right vehicle matched to the wrong trip. A
wrong-direction display is almost certainly a wrong-trip match whose
headsign the site then faithfully shows. The point of this part is that the
system should be able to *tell you which*, with evidence, without you
having caught it live.

## What exists and what is missing (verified)

- `audit.db` `timepoint_observations` records the delay, GPS distance,
  stop and vehicle — but **not** the match tier, direction or bearing.
  `poll_log` already counts `vehicles_total / candidates / matched /
  obs_written / dropped_insane` per poll, so an insanity *counter* exists;
  an insanity *explanation* does not.
- `live.db` `vehicles` is rich (match_tier, direction, bearing,
  low_confidence, distance_m) but is upserted every poll: by the time a
  human notices an anomaly on the site, the matching context that produced
  it has been overwritten. **The evidence evaporates within 30 seconds.**
- The nightly rollup already computes delay histograms per route/operator —
  the aggregation seam this part extends is in place.
- The new SSD makes retaining raw observations and evidence records for
  months cheap. Storage is no longer a reason to discard the history an
  investigation needs.

## Honesty rule (binding, before any code)

The audit's published figures are never silently edited. Anomaly handling
happens at ingest as explicit, documented rules (extending the existing
`dropped_insane` behaviour), or as *flags* on retained data — never as
after-the-fact deletion from published numbers. Any new exclusion rule is a
methodology change: it lands in `AUDIT_METHODOLOGY.md`, its exclusions are
counted and reported, and the date it took effect is recorded so historical
figures remain explainable. "Drop, don't guess" stays; this part adds
"and when you drop or doubt, keep the receipt."

## Work packages

### DQ1 — Nightly measurement-quality report (read-only, start any time)

A networkless Pi job beside the existing rollup, reading `audit.db` and
`poll_log` only, writing one versioned JSON report through
`run_recorded_job.py` like everything else. Checks, all cheap SQL passes:

- **Extreme-delay tail:** every observation beyond configurable bounds
  (suggest > +45 min or < −10 min), listed with route, operator, trip,
  stop and time — not just counted.
- **Direction/progression consistency:** for each observed trip, the
  observations' `stop_sequence` should increase as `recorded_at`
  increases. A trip observed running backwards is a wrong-direction or
  wrong-trip match, mechanically detectable — this check would have caught
  the opposite-direction bus.
- **Physical plausibility:** the same `vehicle_ref` observed at
  implausible speed between consecutive observations (GPS jump or
  identity confusion); the same vehicle on two overlapping trips.
- **Drift metrics:** day-over-day match rate, `dropped_insane` rate, and
  the `gps_distance_m` distribution of *kept* observations. A slow creep
  in any of these is a timetable, feed or matcher problem announcing
  itself quietly.
- **Route-level oddity:** routes whose delay distribution is wildly
  bimodal (a signature of two services being conflated under one line
  number).

Digest output is a sentence or two ("2 extreme delays flagged, 1
backwards trip, match rate steady"), with the detail in the report.

### DQ2 — Anomaly evidence capture (collector, small and bounded)

The collector gains a capped evidence log (new table or file, hard limits
per day and total): when a computed delay exceeds the extreme bounds, when
corroboration keeps flip-flopping journey or direction for one vehicle, or
when a reading is dropped as insane, it records the *why* — the raw SIRI
fields as received, the matched trip and tier, the candidate count, the
GPS distance, the timetable edition. Written on the collector's existing
write path, never touching the audit tables, bounded so a feed meltdown
cannot bloat the database. This is the piece that stops evidence
evaporating: DQ1 finds yesterday's anomalies, DQ2 means the context is
still there when you look.

Also consider recording `match_tier` on `timepoint_observations` itself so
quality can be analysed per tier historically. That is an audit schema
change and therefore coordinates with the methodology doc and every
downstream reader — flagged as a decision, not assumed.

### DQ3 — Investigation tooling and a defect taxonomy

A small attended CLI: given a date and vehicle/trip, assemble everything
known — evidence records, audit observations, the matched schedule, the
candidate schedules it beat. Each investigated anomaly gets a
classification from a growing taxonomy: *genuine severe delay /
wrong-trip match / wrong-direction match / stale re-broadcast / timetable
edition overlap / time-anchor defect / GPS glitch / operator feed defect*.
The taxonomy lives in the repo; every classified case cites its evidence.
Deeper exploratory analysis (distributions, per-operator comparisons over
months) runs on the workstation against a copied `audit.db` — the Pi does
detection, not data science.

### DQ4 — The feedback loop (this is the "self-improvement")

Each *confirmed* defect class turns into something deterministic:

- a regression fixture built from the real captured evidence (the 29 July
  timetable incident already set this precedent — its artifact pair became
  a fixture);
- where justified, a matcher improvement — e.g. if wrong-direction
  matches are confirmed, a candidate whose direction contradicts the
  vehicle's sustained SIRI bearing along the route shape loses eligibility
  (a tightening of "drop, don't guess", never a loosening);
- where justified, a corroboration rule — e.g. extreme delays require an
  extra consecutive confirming poll before an event is emitted for the
  bot, so the persona never posts a 68-minute banger that was actually a
  matching artefact;
- and where an ingest exclusion is added, the methodology doc entry and
  its counted exclusions, per the honesty rule.

**Plain English:** today, a weird reading on the map is a mystery you have
to catch red-handed. After this: every night the Pi re-reads what the audit
recorded and flags readings that don't make physical sense — buses running
backwards, impossible jumps, absurd delays. When something is flagged, the
collector has already kept a snapshot of *why it believed what it
believed* at that moment. You (or a working session with an AI assistant)
then look at the evidence, name the failure mode, and the fix ships with a
test built from the real case so that failure mode is dead permanently.
The published punctuality numbers are never quietly rewritten — rules
change in the open, in the methodology document.

## Acceptance

- DQ1 runs for two weeks report-only; its flags are checked by eye and the
  known real anomalies (a 68-minute delay, a wrong-direction display)
  would have been caught by its checks — demonstrated against historical
  data if still present, else against synthetic fixtures.
- DQ2's evidence log is provably bounded under a hostile feed (fixture
  with thousands of insane readings) and adds no measurable latency to the
  poll path.
- At least the first three investigated anomalies are classified with
  evidence, and at least one produces a regression fixture or matcher
  improvement.
- No published figure changes without a corresponding methodology entry.

---

# Part 6 — Addendum: the long-term Rust consolidation

This addendum adapts an earlier assistant-produced migration plan the
maintainer wants kept. Its shape is kept; its claims were re-checked
against the codebase on 31 July 2026 and several are corrected below,
openly, because a long-term plan built on misreads compounds. **Nothing in
this part starts until Parts 1–5 are implemented and stable.** It is
direction, not commitment.

## What the original plan gets right (kept)

- The **ordering philosophy**: blast radius grows only as Rust confidence
  does; each phase teaches what the next needs; stopping partway still
  banks wins.
- **Leave `pipeline/` and `deploy/` in Python** — offline, GitHub-CPU'd,
  XML-wrangling and orchestration glue respectively. Knowing what not to
  rewrite is the best paragraph in the original. Fully agreed.
- The **end state**: one Cargo workspace (`collector`, `bot`, `site`,
  shared `types` crate), small static binaries under systemd, and
  cross-component contracts checked at compile time. The shared-types
  argument is real: today the collector, site and bot agree on the event
  schema, delay semantics and stop model only by convention across SQLite
  and JSON, enforced by tests and discipline rather than a compiler.
- **Shadow-run promotion with the old implementation kept as rollback** —
  exactly this project's philosophy applied to a language migration.
- **`WatchdogSec` + `sd_notify`** as a stronger liveness guarantee than
  ask-only health endpoints.

## Corrections after reading the code

1. **`pipeline/compare_collectors.py` is not a comparison harness.** It is
   a compatibility shim forwarding to `check_collector_freshness.py` for
   older staleness units. The "promotion mechanism you already built"
   does **not** exist and must be written. This changes Phase 2's cost,
   not its design.
2. **The bot has no `config: any` looseness.** `bot/tsconfig.json` is
   `strict: true` with strict null checks; the config module is typed.
   The bot is ~10,300 lines of disciplined TypeScript implementing the
   persona pipeline, editorial gates, factual verifier, provenance
   recording and rate limits. It is operationally the lowest-stakes
   component, but it is the **largest behavioural surface to reimplement
   faithfully** — the opposite of a classroom exercise. Rewriting it
   first risks a long, subtle parity chase on the least type-unsafe code
   in the project.
3. **Dark-run diffing of the bot cannot diff "output".** Post text comes
   from Gemini and is non-deterministic. A shadow bot can only be diffed
   on its deterministic decisions: which event it selected, which gates
   passed, what prompt it assembled. Meaningful, but much weaker
   verification than the collector's fully deterministic diff.
4. **A live shadow collector violates invariant #1** (one poller; nothing
   but the collector talks to BODS) and doubles feed load. The fix is
   better than the original idea anyway: **replay, don't double-poll** —
   see R1 below. This also synergises directly with Part 5's DQ2 raw
   evidence capture.
5. **"Marker gliding" already exists.** `app.js` animates markers along
   the route shape (`animateAlongRoute` / `animateStraightLine`).
   Phase 4's list was written against an older mental model of the
   frontend.
6. **SSE is not "the biggest responsiveness win".** The upstream feed
   changes at most every 30 seconds and the frontend already polls every
   15, so perceived latency is bounded by BODS, not by transport. SSE's
   real wins are payload (deltas), battery and request overhead. And it
   is **not currently a small Flask change**: the site runs gunicorn with
   two sync workers, so two open SSE connections would wedge the entire
   site. SSE requires a worker-model change (async/threaded workers or a
   dedicated streaming process) plus keep-alive heartbeats through the
   Cloudflare tunnel. Worth doing eventually; not the casual first step
   the original suggests.
7. **The collector's speed is not a demonstrated constraint.** Measured
   p95 RSS: site ~170 MiB, bot ~131 MiB, collector ~68 MiB, tunnel
   ~35 MiB on a 904 MiB Pi. There is no evidence the Python collector
   misses its 30-second budget — and `bbb-resource-sample` exists to
   prove or disprove that before believing any performance claim. The
   honest case for Rust on this hardware is **RAM headroom** (perhaps
   250+ MiB reclaimed across the three services), **compile-time
   contracts**, and the watchdog — not matching speed.
8. **Rust is not required for `sd_notify`.** Python and Node services can
   adopt watchdog liveness today. Taking it now also de-risks the later
   migration (the units are already watchdog-shaped before any rewrite).
9. **Don't compile Rust on the Pi.** 904 MiB of RAM makes cargo builds
   miserable. Cross-compile ARM64 static binaries in CI — which happens
   to fit the immutable, hash-manifested release model perfectly: a
   static binary is the ideal release artifact for `push.py`.
10. **Frontend TypeScript conversion is a philosophy change, not a
    detail.** The docs deliberately exclude frontend build machinery
    ("native ES modules are the right size"). Most of the type-safety win
    is available with **zero build step** via JSDoc annotations checked
    by `tsc --checkJs` in CI — do that first, and treat a full TS
    toolchain as its own deliberate decision later. Similarly, MapLibre
    GL replaces the project's only third-party browser dependency (Carto
    raster tiles) with a heavier vector stack — defer until marker count
    is a *measured* problem; icon caching and pooling already exist.

## Revised phases

### R0 — No-Rust groundwork (cheap, can land during Parts 1–5)

- `sd_notify` watchdog on the existing collector, site and bot units.
- **Raw SIRI poll archive**: the production poller tees each raw response
  to dated files on the SSD (bounded retention — even a month is only
  modest gigabytes). This is DQ2's big sibling and the enabler of the
  entire migration: a corpus of real, replayable feed days including the
  weird ones.
- Confirm via resource sampling what the collector's poll cycle actually
  costs, so the migration's claims are grounded in this system's numbers.

### R1 — The classroom: a replay harness and a Rust matcher prototype

Instead of rewriting the bot to learn Rust, build the **offline replay
harness**: a Rust crate that parses archived SIRI XML (serde,
compile-time types), runs trip matching against a timetable.db copy, and
emits match decisions and delays in a diffable format; plus the Python
side of the differ. This teaches ownership, serde, error handling and the
actor/channel structure on *the exact problem that matters*, with zero
production presence — a prototype that can be thrown away without loss.
`COLLECTOR_SPEC.md` is the contract; Part 5's DQ fixtures and classified
anomalies are the acceptance corpus. The "no confident match, no delay"
rule becomes an enum the compiler enforces, as the original plan said —
proven here first.

(If a greenfield production classroom is wanted instead, the Part 3
social curation service is the right candidate — small, isolated,
harmless on failure — at the cost of revisiting the Python-orchestrator
recommendation in S2.)

### R2 — Collector migration (the real prize, reframed)

Replay-first: the Rust collector must reproduce the Python collector's
match decisions and delay values across weeks of archived days —
including ghost vehicles, midnight rollovers, BST/GMT boundaries and
every classified anomaly from Part 5 — with every divergence explained
before it goes near production. Then a **short live tee** (the Python
poller feeds the same parsed snapshots to the Rust process in shadow;
BODS still sees one poller, preserving invariant #1), then promotion
through the existing health-gate machinery with the Python collector
retained as the rollback path. The nightly differ the original plan
thought existed gets built in R1 and reused here.

### R3 — Bot (optional, honestly assessed)

The bot rewrite's payoff is footprint (~131 MiB of Node) and eventual
shared-types membership — not robustness, which strict TypeScript
already provides. Do it after the collector, dark-running on the real
event stream and diffing deterministic decisions only. It is legitimate
to conclude the bot stays TypeScript indefinitely; the shared `types`
crate can still emit a JSON Schema the TS side checks at build time,
which buys most of the contract safety without the rewrite.

### R4 — Site backend

The SSE/snapshot work stands on its own merits once the worker model is
addressed, whenever the efficiency win is wanted. The axum migration is
justified **only** by the shared-types payoff, so it follows the
collector (and bot, if rewritten). The original plan's judgement that
per-request read-only SQLite is fine is confirmed by the code — that was
never the reason to move.

### R5 — Frontend

JSDoc + `checkJs` typing first (no build step, philosophy intact), SSE
deltas when R4 lands, Leaflet until measured marker pain. Gliding
already exists; polish it rather than re-plan it.

## Gates and kill criteria

Starts only after Parts 1–5 are stable in production. Each phase must
pay for itself: R0 and R1 are cheap and independently valuable (the
replay corpus improves Part 5 even if no Rust ever ships). R2 proceeds
only if R1's prototype demonstrates clean equivalence on the corpus.
Stopping after R2 banks the RAM headroom and the compile-enforced
matcher — the original plan's "biggest wins" claim, which survives
correction. R3–R5 are optional quality-of-life, each with its own
decision point. Rewriting working, tested, specified software is a cost;
every phase must beat "spend the same evenings on Parts 1–5 polish"
to proceed.

---

# Pi and scheduling considerations

- The new SSD removes the old space anxiety: staging candidates, `.previous`
  copies, pending batches and the enrichment directory all live comfortably
  under `/var/lib/bristolbusbot` and inside the existing restic scope (add
  `enrichment/` to the backup include list explicitly — and to the backup
  manifest test, per the master plan's risk register).
- RAM (~904 MiB) is the real constraint and none of this work challenges it:
  every new job is network + JSON. Nothing here rebuilds databases on the Pi.
- Timer placement: enrichment jobs must not overlap the timetable shadow
  (~5.5 min heavy), backup, or audit publication. Reuse the maintenance lock
  with deadlines and named refusals, per existing policy. Suggested cadence:
  fleet weekly (small hours, offset from backup), data-health audit nightly,
  blurb generation only on detection.
- The Slack curation poll is frequent (every 2–5 minutes) but tiny, and it
  reads only its own inputs and writes only its own directory — it must
  **not** take the maintenance lock. Only deploying the social component
  does. Renders are seconds of CPU on demand, not scheduled load.
- New secrets on the Pi: `GEMINI_API_KEY` in `/etc/bristolbusbot` (0600) for
  enrichment, and the Slack app token delivered to the curation unit alone
  via systemd `LoadCredential`. Neither appears in logs or releases; cost
  ceilings for Gemini live in config. Rotate deliberately.
- Every new unit follows the house pattern: sandboxed oneshot +
  `Persistent=true` timer, `run_recorded_job.py` job records, aggregate
  health, digest sentences. No cron, no new monitoring seams.

# Suggested order of execution

| # | Work | Depends on | Rough size |
|---|---|---|---|
| 0 | Part 0: editorial workflow fix | nothing — **do first** | under an hour |
| 1 | Part 2: status-filter chips | nothing | 1–2 sessions |
| 2 | WP0: Phase A hardening proof | in flight | already planned |
| 3 | WP1: vehicle identity | WP0 | 2–3 sessions |
| 4 | WP2: bot consumer paths | — (parallel with WP1) | 1 session |
| 5 | S1: renderer single-card mode | nothing (parallel track) | 1–2 sessions |
| 6 | S2–S4: curation service, Slack, isolation | S1 | 3–4 sessions + shadow |
| 7 | WP3: decoupling + promotion helper | WP1, WP2 | 2–3 sessions |
| 8 | WP4: data-health audit (report-only) | WP3 | 2 sessions + 2-week soak |
| 9 | WP5: fleet regenerator | WP4 soak | 2 sessions + shadow cycle |
| 10 | WP6: blurb generation + review loop | WP5 | 2–3 sessions + 30-day window |
| 11 | S5: engagement shortlist | S2–S4 proven | 1 session |
| 12 | Part 4: Threads shadow onward | S4 pattern proven | per `SOCIAL_EXPANSION_PLAN_V2.md` |
| 13 | DQ1: measurement-quality report | nothing (read-only) | 2 sessions + 2-week report-only |
| 14 | DQ2: collector evidence capture | WP0 (collector is protected) | 1–2 sessions |
| 15 | DQ3: investigation CLI + taxonomy | DQ1 | 1–2 sessions, then ongoing |
| 16 | DQ4: fixtures + matcher improvements | evidence from DQ1–DQ3 | ongoing, per confirmed case |

"Session" = one focused working evening with tests. The soak and review
windows are calendar time, not effort, and they overlap other work.

The social track (S1–S5) runs in parallel with the enrichment track rather
than queueing behind it, per V2 §7's per-feature gates: it touches no
enrichment data, no BODS path and no shared lock outside deployment. The
two tracks meet only at deploy time — don't land a social component deploy
and an enrichment cutover in the same evening, purely so any alert has one
suspect.

The measurement-quality track starts with DQ1 early — it is read-only and
independent, and every week it runs is a week of evidence accumulating.
DQ2 waits for WP0 because it touches the collector, the most protected
component in the system, and mid-incident-correction is the wrong moment.

Part 6 (the Rust consolidation) sits entirely after this table, with one
exception: its R0 groundwork — watchdog liveness and the raw SIRI poll
archive — is cheap, Rust-free, and independently valuable to Part 5, so
it may land alongside the DQ work whenever convenient.

# Decisions to make before implementation

1. **Filter select mode** — single-select (recommended v1) vs multi-select.
2. **Filter × route view composition** — mutually exclusive (recommended v1)
   or composable later.
3. **Blurb approval UX** — attended SSH review command (recommended: smallest
   surface, no new credentials) vs a GitHub-PR flow like editorial (nicer
   diff view, but requires a Pi write-credential or a manual upload step).
4. **New-model handling** — skip until a human writes the model-context entry
   (recommended) vs generate a low-context blurb immediately.
5. **Slack app** — extend the existing BristolBusBot app with the narrow
   scopes (recommended if its install allows) vs a second dedicated app
   (cleaner blast radius, one more thing to manage).
6. **Shortlist cadence** — daily digest-style message (recommended) vs
   weekly, and whether it starts at all in v1.
7. **Cards-per-day ceiling** — a small hard cap (e.g. 10) as a brake on
   accidents; pick the number.
8. **Extreme-delay bounds for DQ1/DQ2** — the flagging thresholds (suggest
   > +45 min / < −10 min to start; tune on the first fortnight's report).
9. **Add `match_tier` to `timepoint_observations`** — better forensics
   forever after, but it is an audit schema change coordinated with the
   methodology doc and every downstream reader (recommended: yes, done
   deliberately as its own change).

# Risks specific to this plan

| Risk | Mitigation |
|---|---|
| Scheduling the existing scripts as-is quietly degrades data | The whole point of WP1–WP5 ordering; nothing is scheduled until it has the candidate contract |
| Fleet-code collision cross-wires blurbs at automation speed | WP1 before any generation; collision fixtures |
| Prompt injection via bustimes free-text fields | Data-not-instructions framing + deterministic output gates + whole-batch discard |
| Filter UI and counts disagree | One shared `statusOf()` classifier with tests |
| Marker icons don't refresh on filter toggle | Filter state included in the icon cache key (step 4) |
| Gemini spend creep | Per-run and monthly ceilings in config, spend surfaced in digest |
| New timers collide with timetable/backup | Shared maintenance lock, deadlines, named refusals — existing policy, extended not reinvented |
| A crafted Slack message gets a fake quote rendered | Slack text is never rendered; the link is only a lookup key; provenance comes solely from `app_data.db` |
| Slack token compromise | Narrowest scopes, one private channel, root-only systemd credential, deliberate rotation; worst case is reading/writing one drafts channel |
| Duplicate or lost card deliveries | `social.db` ledger unique on (post URI, kind); reconcile-before-retry; Slack's 90-day history is never the record |
| resvg/sharp native builds drift on ARM64 | S1's Pi smoke test is a standing part of the social component's deploy checks |
| Bluesky AppView unavailable | Shortlist degrades to recency or skips; main flow never depends on it |
| Social failure bleeding into production services | S4 sandboxing + kill-test acceptance; no shared writable paths, no shared credentials |
| Anomaly handling quietly rewrites published figures | The honesty rule: ingest-time documented rules or flags only; every exclusion counted, dated and in `AUDIT_METHODOLOGY.md` |
| Evidence capture bloats the collector DB under a bad feed | Hard per-day and total caps, hostile-feed fixture in tests, no writes to audit tables |
| DQ checks add load to the poll path | Detection runs nightly off-path; only the bounded evidence write touches the collector, measured before/after |

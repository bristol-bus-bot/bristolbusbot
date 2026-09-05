# Shared collector

The only production process that talks to BODS. It polls SIRI-VM and SIRI-SX,
matches vehicles against the shared timetable, computes observed delays, and
writes:

- `live.db`: current vehicles, disruptions, health and corroborated bot events
- `audit.db`: closest-approach timing-point observations plus bounded private
  receipts for suspicious matching decisions

Run locally from this directory:

```powershell
python -m pip install -e ".[dev]"
$env:BBB_TIMETABLE_DB="C:\path\to\timetable.db"
python -m collector.run
pytest
```

One-off cancellation-feed check (aggregate output only):

```powershell
python -m collector.check_cancellations
```

This reads the dedicated BODS journey-cancellation endpoint. It does not add a
second poller, save the raw feed, or treat an absent record as proof that a bus
ran. A zero for an operator means only that the operator supplied no records to
this endpoint at the time of the check. The defaults check both First's current
WECA operator code (`FBRI`) and its ceased historical code (`FSAV`). Because an
operator code alone does not prove the affected journey is in WECA, the output
also reports journeys touching WECA's four ATCO stop-reference prefixes. This
catches a record filed under an unexpected operator code without publishing
individual journey or stop identifiers.

Production:

- Current release: `~/bristolbusbot/current/collector` on the Pi
- Durable databases: `/var/lib/bristolbusbot/collector`
- systemd unit: `bbb-collector.service`
- Deploy: `python deploy/push.py --component collector` from the repository root

The deploy cannot package the Pi-owned config or databases, verifies the full
release manifest, atomically switches code, restarts only the collector and
requires database checks plus a fresh successful SIRI poll. The staleness and
status-digest jobs are systemd timers.

## Private matching diagnostics

Diagnostic receipts are a selected investigation sample, not a count of bad
buses or a representative sample of all journeys. Ordinary collection and
punctuality calculations continue independently of diagnostic admission.

The store keeps the existing ceilings of 250 receipts per local day, 5,000
receipts overall and three alternative candidates per receipt. Admission is
split into six four-hour Europe/London bands and primary anomaly reasons.
Each band/reason has at most ten receipts, with at most four per audited
operator and two per vehicle/chosen-trip/reason each day. The final place in
a band/reason is reserved for a previously unseen operator/route/journey
reference. These are upper bounds, not targets: unused capacity is not filled
by more overnight repeats.

Sampling uses the recorded position's local date, falling back to a valid
capture timestamp. It never charges the matched timetable date, which can be
wrong. Existing receipts without sampling metadata count towards the daily
ceiling by their capture instant, including across BST midnight. Additive
schema changes preserve old observations and permit rollback to old code.

Receipts retain the feed's origin and destination stop references alongside
the supplied direction and chosen/alternative timetable directions. Poll
counters distinguish saved receipts, repeats, excluded operators, full quotas
and diagnostic errors. Operators outside the audit shortlist still have their
normal live vehicles and observations collected; only their diagnostic
receipts are excluded.

After release, check a complete local day's distribution by `sampling_band`,
`sampling_reason` and operator, and reconcile the poll counters. Storage must
remain bounded and useful daytime cases must survive before considering the
sampling change verified in production. Older receipts have null sampling
fields; newly added poll counters are zero for older polls and do not recreate
historical admission outcomes.

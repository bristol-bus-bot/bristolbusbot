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

# Shared collector

The only production process that talks to BODS. It polls SIRI-VM and SIRI-SX,
matches vehicles against the shared timetable, computes observed delays, and
writes:

- `live.db`: current vehicles, disruptions, health and corroborated bot events
- `audit.db`: closest-approach timing-point observations

Run locally from this directory:

```powershell
python -m pip install -e ".[dev]"
$env:BBB_TIMETABLE_DB="C:\path\to\timetable.db"
python -m collector.run
pytest
```

Production:

- Current release: `~/bristolbusbot/current/collector` on the Pi
- Durable databases: `/var/lib/bristolbusbot/collector`
- systemd unit: `bbb-collector.service`
- Deploy: `python deploy/push.py --component collector` from the repository root

The deploy cannot package the Pi-owned config or databases, verifies the full
release manifest, atomically switches code, restarts only the collector and
requires database checks plus a fresh successful SIRI poll. The staleness and
status-digest jobs are systemd timers.

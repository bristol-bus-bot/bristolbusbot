# bristolbuses.live

The Flask/Leaflet live map and departure board. It is a read-only consumer of
the shared collector's `live.db` and the validated timetable database.

Run locally:

```powershell
python -m pip install -e ..\collector -e ".[dev]"
$env:BBB_LIVE_DB="C:\path\to\live.db"
$env:BBB_TIMETABLE_DB="C:\path\to\timetable.db"
python wsgi.py
```

Open `http://127.0.0.1:5000`. Production HTTPS enforcement is enabled only by
`BBB_ENFORCE_HTTPS=true`; direct localhost readiness checks remain available.

Production:

- Public URL: `https://bristolbuses.live`
- Current release: `~/bristolbusbot/current/site` on the Pi
- systemd unit: `bbb-site.service` (gunicorn on `127.0.0.1:5002`)
- Tunnel: `bbb-tunnel.service`
- Deploy: `python deploy/push.py --component site` from the repository root

The release includes the collector-library snapshot used by the site. The
deploy restarts only the site, checks data-aware readiness and automatically
restores the previous release on failure. It never modifies or restarts the
named tunnel.

## Browser privacy and third-party requests

Fonts and Leaflet JavaScript/CSS are served by bristolbuses.live itself.
The live basemap is the one intentional browser-side third-party dependency:
map image tiles are fetched from Carto at `*.basemaps.cartocdn.com`. As with
any remote image host, Carto receives the visitor's IP address, user agent and
the tile coordinates requested by their browser. Carto supplies map imagery
only; no Carto JavaScript, fonts or tracking code is loaded. The Content
Security Policy therefore permits Carto only in `img-src`.

Font licences are retained in `static/fonts/`; Leaflet's BSD licence is
retained in `static/vendor/leaflet-1.9.4/`.

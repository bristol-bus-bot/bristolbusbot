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
Local map rendering also needs the project-only `BBB_CARTO_BASEMAP_KEY`.

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

## Map marker language

The marker centre is the live running state: a green circle is on time, a red
square is late, an amber upward triangle is early, a blue doughnut is waiting
at the route origin, and a small grey dot is at a depot. The surrounding ring
is the vehicle livery and the outside nose points in its reported direction of
travel. A yellow corner tag means the bot posted about that journey. Route and
status filters redraw non-matches as outlines while retaining their status
colour and shape. The visible moving-bus mark is 32 px inside a 44 px pointer
target for mobile use.

Fleet identity is registration-first and otherwise scoped by
`(operator, fleet code)`. Shared numbers never fall through to another
operator's model or livery. Legacy bare-code descriptions remain available for
unambiguous vehicles; an ambiguous description is omitted until a reviewed
operator-scoped key such as `FBRI:36801` exists.

## Browser privacy and third-party requests

Fonts and Leaflet JavaScript/CSS are served by bristolbuses.live itself.
The live basemap is the one intentional browser-side third-party dependency:
map image tiles are fetched from CARTO at `*.basemaps.cartocdn.com`. As with
any remote image host, CARTO receives the visitor's IP address, user agent and
the tile coordinates requested by their browser. CARTO supplies map imagery
only; no CARTO JavaScript, fonts or tracking code is loaded. The Content
Security Policy therefore permits CARTO only in `img-src`.

The free CARTO key is deliberately supplied by the protected production
environment and rendered as a `data-` attribute on the map. It is not a true
secret: every visitor's browser must send it directly to CARTO. It must still
stay out of Git, issues and normal logs so it is not casually reused.

To install or rotate it, run `python deploy/configure_carto_key.py` from the
repository root and paste the complete CARTO tile URL from the key email into
the hidden prompt. The command validates the URL, stores nothing locally,
stages a mode-0600 candidate on the Pi and invokes one allowlisted promotion.
The Pi validates the whole site environment, restarts the site, checks health
and restores the previous file automatically on failure. To revoke a key,
contact `support-basemaps@carto.com`, request a replacement, then run the same
command with the replacement URL.

Review CARTO's monthly usage information for this project once a month. At
2.5 million tile requests in a calendar month, reopen the basemap-provider
decision rather than waiting for the five-million fair-use limit. Direct
browser tile traffic does not pass through the Pi, so the Pi access log is not
an authoritative CARTO usage counter.

Font licences are retained in `static/fonts/`; Leaflet's BSD licence is
retained in `static/vendor/leaflet-1.9.4/`.

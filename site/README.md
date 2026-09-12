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
target for mobile use. A grey centre inside the livery ring means timing is
unavailable; it is distinct from the small depot dot. These buses remain
visible and have their own count/filter instead of being counted as on time.
The API preserves a missing delay as `delayMinutes: null` with
`eventType: "unknown"`, including when a timetable match exists but no reliable
delay was calculated. Genuine zero-minute delays remain on time.

Fleet identity is registration-first and otherwise scoped by
`(operator, fleet code)`. Shared numbers never fall through to another
operator's model or livery. Legacy bare-code descriptions remain available for
unambiguous vehicles; an ambiguous description is omitted until a reviewed
operator-scoped key such as `FBRI:36801` exists.

## Browser privacy and third-party requests

Application fonts, Leaflet, MapLibre GL JS and the Leaflet bridge are served
by bristolbuses.live itself. Leaflet continues to own the camera, bus markers,
stop markers, popups and route lines. MapLibre paints the background only:
Voyager by day and Dark Matter at night. Theme changes call `setStyle()` on the
same background map, preserving overlays and camera position.

CARTO is the intentional browser-side third-party dependency. The browser
fetches styles, vector tiles, sprites and map-label glyphs from
`basemaps.cartocdn.com` and its subdomains. CARTO receives the visitor's IP
address, user agent and requested map coordinates. No remote executable code
is loaded. CSP permits these resources in `connect-src`; module workers and
scripts remain same-origin, without `unsafe-eval` or blob worker permission.

If module loading, WebGL or a map resource fails, or initial/style loading
stalls for 15 seconds, the background falls back to the existing keyed CARTO
raster service for that page visit. Bus overlays and Leaflet controls survive;
theme changes still work. A generic console warning and the map element's
`data-basemap` (`loading`, `vector`, or `raster`) allow diagnosis without
logging the key. Raster is a temporary compatibility path while the provider
continues to serve it. A failed server release uses the normal atomic rollback.
MapLibre uses two workers and caps canvas pixel density at 2 to bound mobile
rendering cost; real-device responsiveness and heat still need direct testing.

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

Font licences are retained in `static/fonts/`; library licences are retained
next to each pinned version in `static/vendor/`. See `BASEMAP.md` for asset
provenance and the browser verification procedure.

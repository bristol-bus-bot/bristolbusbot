# Background map integration

Leaflet remains the interactive map. `static/js/basemap.js` lazily imports the
pinned MapLibre ES module and browser bridge, then creates one non-interactive
vector layer in Leaflet's tile pane. Import failure cannot prevent bus data
or controls from initialising. The controller attaches the existing project
key only to HTTPS CARTO resources and owns fallback and theme switching.

## Vendored provenance

These files are copied unchanged from official npm tarballs:

- `maplibre-gl@6.6.0`: `https://registry.npmjs.org/maplibre-gl/-/maplibre-gl-6.6.0.tgz`
- `@maplibre/maplibre-gl-leaflet@0.1.4`: `https://registry.npmjs.org/@maplibre/maplibre-gl-leaflet/-/maplibre-gl-leaflet-0.1.4.tgz`

Development bundles and source maps are omitted. Production ES modules share
their unchanged relative imports and worker URL inside the content-versioned
asset graph. Gzip sizes below describe the files, not measured network traffic.

| File | Bytes | Gzip bytes | SHA-256 |
|---|---:|---:|---|
| `maplibre-gl-6.6.0/LICENSE.txt` | 5,984 | 1,530 | `ee5fc05a0677eaf69601d2c7db0d9ecd6cc27c3abc1d0733bc9ed34707cf8ef2` |
| `maplibre-gl-6.6.0/maplibre-gl-shared.mjs` | 489,575 | 135,559 | `34c2cb0330cec92e81c4fa7344e7008451442bbb9cca1da3465db4041a934073` |
| `maplibre-gl-6.6.0/maplibre-gl-worker.mjs` | 18,592 | 5,890 | `b081c9b3d0691d9d85552b5624f2601f69f24ed37573959d279d322e98e4ee2f` |
| `maplibre-gl-6.6.0/maplibre-gl.css` | 83,195 | 10,474 | `8e2dbbab312dc57656fbb76e9fa5308c75c9d7c7ba5808a7d55bcdb64cc813fa` |
| `maplibre-gl-6.6.0/maplibre-gl.mjs` | 568,135 | 142,370 | `d84cb65fa75f07a972616cb4ee1902829ca053beae28f7be9b0889131c497afd` |
| `maplibre-gl-leaflet-0.1.4/leaflet-maplibre-gl.js` | 9,296 | 2,674 | `1e6cf8cb3eb5fd909879aa1bf36a383fb506c9a5b2dbbfababce65a294dd1fcb` |
| `maplibre-gl-leaflet-0.1.4/LICENSE` | 767 | 489 | `eaa721ba158cbeff47ad53b1035dfc26ff744df66662c93ff715c9885197ebf3` |

## Verification when changing the background

Run the site Python and JavaScript suites. In a browser verify day/night,
marker identity, livery and twelve-second glide, stop culling/selection,
route selection and lines, boundary/layer toggles, flyTo/fitBounds, location,
pan/zoom and mobile sheet/orientation resizing. Check that attribution stays
visible, the GL camera tracks Leaflet and the normal path requests no raster
PNG tiles. Compare uncached and cached loads at 390px width.

Exercise missing modules, blocked style requests, a failed WebGL constructor,
context loss and a stalled load. Each should leave one raster background with
working overlays and day/night switching, without a script exception. Record
actual older-phone responsiveness, memory/heat and longer-running behaviour
separately from desktop emulation; emulation is not real-device acceptance.

Provider guidance: https://docs.carto.com/faqs/carto-basemaps
Bridge: https://github.com/maplibre/maplibre-gl-leaflet

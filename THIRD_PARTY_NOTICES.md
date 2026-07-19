# Third-party notices

Code, fonts and data this project incorporates or adapts. Data-source
attribution required by the Open Government Licence is in
`ATTRIBUTION.md`; this file covers the code and asset licences.

## Adapted code

### bustimes.org TransXChange parser

`pipeline/txc_parser.py` is adapted from the TransXChange parser in
bustimes.org:
https://github.com/bustimes/bustimes.org

Upstream is licensed under the Mozilla Public License 2.0 (MPL-2.0):
https://www.mozilla.org/en-US/MPL/2.0/

Local modifications replace the Django/GEOS dependencies with local
equivalents. That file remains MPL-2.0-covered and carries the standard MPL
notice and SPDX identifier. The rest of the project's original
code is covered by the root AGPL-3.0-only licence.

## Vendored libraries

### Leaflet 1.9.4

`site/static/vendor/leaflet-1.9.4/` — © Volodymyr Agafonkin and
contributors, BSD 2-Clause licence. The licence text is retained
alongside the vendored files.

## Fonts

### Overpass

Self-hosted variable WOFF2 in `site/static/fonts/` and a subset in
`audit-site/fonts/`. Licensed under the SIL Open Font License 1.1.

### JetBrains Mono

Self-hosted variable WOFF2 in `site/static/fonts/` and a subset in
`audit-site/fonts/`. © JetBrains, licensed under the SIL Open Font
License 1.1.

Licence texts for both fonts are in `site/static/fonts/LICENSES.txt`.

## Data used by the code

Full attributions in `ATTRIBUTION.md`; in summary:

- **BODS** (Department for Transport) — timetables, real-time vehicle
  locations and disruptions. Open Government Licence v3.0.
- **Traveline National Dataset (TNDS)** — supplementary timetables. OGL.
- **NaPTAN** — bus stop locations and codes. OGL v3.0.
- **Bristol City Council, Bristol Bus Stops** — local stop-name and facility
  enrichment. Open Government Licence.
- **ONS Open Geography Portal** — ward and unitary-authority boundaries,
  fetched by `pipeline/download_weca_boundary.py`. OGL v3.0; contains
  OS data © Crown copyright and database right.
- **bustimes.org** — community-maintained fleet data (vehicle models,
  liveries, registrations), fetched at runtime, cached locally outside Git,
  and displayed with attribution and links back. The raw vehicle dataset is
  not redistributed in this repository.

## Services

- **Carto** supplies map-image tiles to visitors' browsers (the one
  intentional third-party browser dependency; see `site/README.md`).
- **Bluesky** public APIs for bot posting and post display.
- **OpenWeather** for weather context in bot commentary.

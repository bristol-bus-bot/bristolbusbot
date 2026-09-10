# Depot boundaries

`app/data/depot_boundaries.geojson` contains six WGS84 yard polygons reviewed
by the maintainer on 10 September 2026. Coordinates are longitude, latitude.
The site classifies a reported vehicle position inside a yard as being at that
depot. This does not establish its allocated garage or how long it has been
there; GPS accuracy still matters. There is no extra radius or buffer.

Lawrence Hill and Bath (Weston Island) use OpenStreetMap ways
[26471909](https://www.openstreetmap.org/way/26471909) and
[89626804](https://www.openstreetmap.org/way/89626804), respectively, retrieved
on 10 September 2026. The other four boundaries were drawn by the maintainer.
The boundary dataset is available under the
[Open Database Licence](https://opendatacommons.org/licenses/odbl/1-0/),
with attribution to OpenStreetMap contributors and the project maintainer.

Update the GeoJSON when a yard changes, retaining source attribution and the
review date. Run the depot tests, including the neighbouring roadside stops,
before release. Geometry changes should be reviewed against the actual yard;
do not enlarge polygons just to include an uncertain GPS position.

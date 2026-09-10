"""Reviewed yard coverage and neighbouring NaPTAN roadside regressions."""
import json
from pathlib import Path

import pytest
from app.services.depots import check_depot, _in_ring

INTERIORS = [('Lawrence Hill', 51.4605445, -2.5659439289572177),
 ('Hengrove', 51.42037266802278, -2.5873104437225694),
 ('Bath (Weston Island)', 51.38200345, -2.3948805472715113),
 ('Weston-super-Mare', 51.342495671292696, -2.957492621337506),
 ('Keynsham (Gypsy Ln)', 51.392939997788645, -2.47857451142655),
 ('Eurocoaches Yard', 51.45591560698308, -2.568835599223033)]
ROADSIDE_STOPS = [('bstjpga', 51.46104, -2.56769),
 ('bstgwgw', 51.41953, -2.58786),
 ('bstgwjt', 51.41946, -2.58784),
 ('bstpjtg', 51.41963, -2.58528),
 ('bthdpmp', 51.38239, -2.39456),
 ('bthdptg', 51.38257, -2.39342),
 ('bthmwgm', 51.38081, -2.3935),
 ('bthpdjd', 51.38069, -2.39339),
 ('wsmapmw', 51.34194, -2.95744),
 ('wsmapta', 51.34181, -2.95761),
 ('wsmpapt', 51.34156, -2.95555),
 ('bthpdpt', 51.3911, -2.47901),
 ('bthpdpw', 51.39103, -2.47894)]

@pytest.mark.parametrize('name,lat,lon', INTERIORS)
def test_each_reviewed_yard(name, lat, lon):
    assert check_depot(lat, lon) == name

@pytest.mark.parametrize('code,lat,lon', ROADSIDE_STOPS)
def test_neighbouring_public_stops_are_not_depots(code, lat, lon):
    assert check_depot(lat, lon) is None, code

@pytest.mark.parametrize('lat,lon', [(None, -2.5), (51.4, None), (float('nan'), -2.5), (51.4, float('inf')), (91, 0), (0, 181)])
def test_invalid_position(lat, lon):
    assert check_depot(lat, lon) is None

def test_concave_ring_and_boundary():
    ring = [(0,0), (4,0), (4,1), (1,1), (1,4), (0,4), (0,0)]
    assert _in_ring(0.5, 3, ring)
    assert _in_ring(3, 0.5, ring)
    assert not _in_ring(2, 2, ring)
    assert _in_ring(1, 2, ring)
    assert _in_ring(4, 0, ring)
    assert not _in_ring(4.00001, 0, ring)
    assert _in_ring(0.5, 3, list(reversed(ring)))

def test_bundled_boundary_contract():
    path = Path(__file__).resolve().parents[1] / 'app/data/depot_boundaries.geojson'
    data = json.loads(path.read_text(encoding='utf-8'))
    assert len(data['features']) == 6
    assert len({f['properties']['id'] for f in data['features']}) == 6
    for feature in data['features']:
        assert feature['geometry']['type'] == 'Polygon'
        for ring in feature['geometry']['coordinates']:
            assert len(ring) >= 4 and ring[0] == ring[-1]
            assert all(-3.1 < lon < -2.2 and 51.2 < lat < 51.7 for lon, lat in ring)

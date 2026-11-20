import json
import os
import time

from app.services.pinch_points import PinchPoints, cut_shape, route_number

STOPS = {
    "tm": (51.4490, -2.5810, "Bristol Temple Meads Station"),
    "cen": (51.4540, -2.5960, "The Centre"),
    "ps1": (51.4560, -2.6010, "Park Street top"),
}
# A road shape for route 1 inbound that passes both stops, with bends between.
SHAPE = [[51.4480, -2.5790], [51.4490, -2.5811], [51.4500, -2.5850],
         [51.4520, -2.5900], [51.4540, -2.5959], [51.4550, -2.5990]]


def section(**overrides):
    item = {"direction": 1, "from": "Bristol Temple Meads Station", "to": "The Centre",
            "from_place": None, "to_place": "Bristol City Centre",
            "from_stop": "tm", "to_stop": "cen", "band": "pm_peak", "day_type": "weekday",
            "kind": "tight", "timetable_min": 8.0, "typical_min": 14.7, "journeys": 177,
            "share_pct": 99, "route": "1 Bristol"}
    return {**item, **overrides}


def write(directory, name, routes, window=None):
    directory.mkdir(exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps({
        "operator": name, "window": window or {"from": "20261023", "to": "20261119"},
        "min_journeys": 30, "routes": routes}), encoding="utf-8")


def reader(tmp_path, shapes=None):
    return PinchPoints(str(tmp_path), lambda: STOPS,
                       lambda: shapes if shapes is not None else {("1", "FBRI", 1): [SHAPE]})


def test_tight_sections_become_lines_following_the_road(tmp_path):
    segments = tmp_path / "segments"
    write(segments, "FBRI", {
        "1 Bristol": [section(), section(kind="slack", typical_min=5.0),
                      section(band="interpeak", typical_min=13.9, timetable_min=7.0, journeys=454)],
        "72": [section(route="72", from_stop="cen", to_stop="ps1", typical_min=11.0,
                       **{"from": "The Centre", "to": "Park Street top"})],
    })
    write(segments, "ALL", {"ignored": [section()]})
    result = reader(tmp_path).get("pm_peak", "weekday")
    assert result["available"] and result["window"]["from"] == "20261023"
    lines = {f["properties"]["from"] + ">" + f["properties"]["to"]: f for f in result["features"]}
    temple = lines["Bristol Temple Meads Station>The Centre"]
    props = temple["properties"]
    assert props["routes"] == [{"route": "1 Bristol", "operator": "FBRI", "timetable_min": 8.0,
                                "typical_min": 14.7, "lost_min": 6.7, "journeys": 177}]
    assert props["approximate"] is False
    assert len(temple["geometry"]["coordinates"]) > 2          # follows the shape's bends
    assert temple["geometry"]["coordinates"][0] == [-2.5810, 51.4490]
    park = lines["The Centre>Park Street top"]
    assert park["properties"]["approximate"] is True           # no shape for route 72
    assert len(park["geometry"]["coordinates"]) == 2
    assert result["counts"] == {"pm_peak:weekday": 2, "interpeak:weekday": 1}
    assert result["features"][-1]["properties"]["lost_min"] == 6.7   # worst drawn on top


def test_shared_stretches_are_grouped_and_missing_stops_counted(tmp_path):
    segments = tmp_path / "segments"
    write(segments, "FBRI", {"1 Bristol": [section()], "8": [section(route="8", typical_min=12.0)],
                             "9": [section(route="9", from_stop="nowhere")]})
    write(segments, "SSWL", {"X1": [section(route="X1", typical_min=16.0)]})
    result = reader(tmp_path).get("pm_peak", "weekday")
    assert len(result["features"]) == 1
    routes = result["features"][0]["properties"]["routes"]
    assert [r["route"] for r in routes] == ["X1", "1 Bristol", "8"]     # worst first
    assert result["features"][0]["properties"]["lost_min"] == 8.0
    assert result["dropped"] == 1


def test_old_missing_or_bad_input_says_so(tmp_path):
    points = reader(tmp_path)
    assert points.get("pm_peak", "weekday") == {"available": False, "reason": "no_current_data"}
    write(tmp_path / "segments", "FBRI", {"1 Bristol": [section()]})
    assert points.get("rush_hour", "weekday")["reason"] == "unknown_band"
    old = time.time() - 4 * 86400
    os.utime(tmp_path / "segments" / "FBRI.json", (old, old))
    assert points.get("pm_peak", "weekday")["reason"] == "no_current_data"


def test_rebuilds_only_when_a_file_changes(tmp_path):
    calls = []
    points = PinchPoints(str(tmp_path), lambda: calls.append(1) or STOPS, lambda: {})
    write(tmp_path / "segments", "FBRI", {"1 Bristol": [section()]})
    points.get("pm_peak", "weekday")
    points.get("am_peak", "weekday")
    assert len(calls) == 1
    time.sleep(0.01)
    write(tmp_path / "segments", "FBRI", {"1 Bristol": [section(typical_min=15.0)]})
    later = time.time() + 1
    os.utime(tmp_path / "segments" / "FBRI.json", (later, later))
    assert points.get("pm_peak", "weekday")["features"][0]["properties"]["lost_min"] == 7.0
    assert len(calls) == 2


def test_cut_shape_refuses_a_shape_that_misses_the_stops_or_runs_backwards():
    start, end = STOPS["tm"][:2], STOPS["cen"][:2]
    assert cut_shape([SHAPE], start, end)[0] == list(start)
    assert cut_shape([list(reversed(SHAPE))], start, end) is None
    far = [[p[0] + 0.01, p[1]] for p in SHAPE]
    assert cut_shape([far], start, end) is None
    assert route_number("1 N Somerset") == "1" and route_number("X39") == "X39"


def test_endpoint(app, client, tmp_path):
    write(tmp_path / "segments", "FBRI", {"1 Bristol": [section()]})
    app.extensions["bbb_pinch_points"] = reader(tmp_path)
    response = client.get("/api/pinch-points?band=pm_peak&day=weekday")
    body = response.get_json()
    assert body["available"] and body["type"] == "FeatureCollection" and len(body["features"]) == 1
    assert response.headers["Cache-Control"] == "public, max-age=900"
    assert client.get("/api/pinch-points?band=am_peak&day=weekend").get_json()["features"] == []

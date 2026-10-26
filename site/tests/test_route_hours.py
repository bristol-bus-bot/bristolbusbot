import json
import os
import time

from app.services.route_hours import RouteHours

PAYLOAD = {
    "operator": "FBRI", "window": {"from": "20260927", "to": "20261024",
                                   "days": {"weekday": 20, "weekend": 8}},
    "min_readings": 30,
    "network": {"weekday": {}, "weekend": {}},
    "routes": {"75": {"weekday": {"8": [30, 2, 20]}, "weekend": {}}},
    "journeys": {"duty_checked": True, "network": {},
                 "routes": {"75": {"total": [3228, 3113, 10], "bands": {}}}},
}


def write(tmp_path, payload=PAYLOAD, name="FBRI.json"):
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")


def test_returns_one_route_with_its_journeys(tmp_path):
    write(tmp_path)
    route = RouteHours(str(tmp_path)).route("FBRI", "75")
    assert route["hours"]["weekday"]["8"] == [30, 2, 20]
    assert route["journeys"]["total"] == [3228, 3113, 10]
    assert route["duty_checked"] is True and route["window"]["days"]["weekday"] == 20


def test_unknown_bad_old_or_broken_files_give_nothing(tmp_path):
    reader = RouteHours(str(tmp_path))
    assert reader.route("FBRI", "75") is None                    # no file
    write(tmp_path)
    assert reader.route("FBRI", "999") is None                   # no such route
    assert reader.route("../etc", "75") is None                  # not an operator code
    old = time.time() - 4 * 86400
    os.utime(tmp_path / "FBRI.json", (old, old))
    assert reader.route("FBRI", "75") is None                    # out of date
    (tmp_path / "FBRI.json").write_text("{broken", encoding="utf-8")
    assert reader.route("FBRI", "75") is None                    # unreadable


def test_endpoint(app, client, tmp_path):
    hourly = tmp_path / "hourly"
    hourly.mkdir()
    write(hourly)
    app.extensions["bbb_route_hours"] = RouteHours(str(hourly))
    body = client.get("/api/route-hours?operator=fbri&route=75").get_json()
    assert body["available"] is True and body["route"] == "75"
    assert client.get("/api/route-hours?operator=FBRI&route=1").get_json() == {"available": False}

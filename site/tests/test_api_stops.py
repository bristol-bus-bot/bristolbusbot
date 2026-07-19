from datetime import datetime
from zoneinfo import ZoneInfo

from unittest.mock import patch

LDN = ZoneInfo("Europe/London")
# Wednesday 1 July 2026, 22:01 local (matches other fixtures)
NOW_LOCAL = datetime(2026, 7, 1, 22, 1, 0, tzinfo=LDN)


def test_stops_list(client):
    data = client.get("/api/stops").get_json()
    by_code = {s["stop_code"]: s for s in data["stops"]}
    assert by_code["0100A"]["common_name"] == "Origin Stop"
    assert by_code["0100A"]["latitude"] == 51.4600  # renamed keys, frontend compat


def test_scheduled_departures_with_calendar_exception(client):
    with patch("app.services.stops.datetime") as dt:
        dt.now.return_value = NOW_LOCAL
        data = client.get("/api/scheduled-departures/0100C").get_json()
    deps = data["scheduled_departures"]
    times = {d["scheduled_time"] for d in deps}
    # T_OUT 22:10 and T_OUT2 22:12 from the weekday calendar,
    # T_EXC 22:20 ONLY via today's calendar_dates addition
    assert {"22:10", "22:12", "22:20"} <= times
    assert all(d["source"] == "scheduled" for d in deps)
    exc = next(d for d in deps if d["scheduled_time"] == "22:20")
    assert exc["destination"] == "Extra Day Trip"
    assert deps == sorted(deps, key=lambda d: d["eta_mins"])


def test_scheduled_unknown_stop_404(client):
    assert client.get("/api/scheduled-departures/nope").status_code == 404

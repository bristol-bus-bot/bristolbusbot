def test_exact_journey_code(client):
    data = client.get("/api/journey-schedule/VJ_2100?operator=FBRI").get_json()
    assert data["destination"] == "Hengrove Leisure Pk"
    assert [s["stop_code"] for s in data["stops"]] == ["0100A", "0100B", "0100C"]
    assert data["stops"][0]["stop_sequence"] == 1


def test_fuzzy_fallback(client):
    # wrong journey code, but line+direction+origin time identify T_OUT
    url = ("/api/journey-schedule/9999?operator=FBRI&line=75"
           "&directionRef=outbound&originAimedDep=2026-07-01T21:00:00%2B00:00")
    from unittest.mock import patch
    from datetime import datetime, timezone
    with patch("app.services.journeys.datetime") as dt:  # inside the age window
        dt.now.return_value = datetime(2026, 7, 1, 21, 5, tzinfo=timezone.utc)
        data = client.get(url).get_json()
    assert [s["stop_code"] for s in data["stops"]] == ["0100A", "0100B", "0100C"]


def test_not_found(client):
    assert client.get("/api/journey-schedule/9999").status_code == 404


def test_stale_journey_refused(client):
    # journey started >2h ago: refuse rather than serve yesterday's
    # timetable as live (regression test for the P1-at-00:27 bug)
    url = ("/api/journey-schedule/VJ_2100?operator=FBRI"
           "&originAimedDep=2026-07-01T10:00:00%2B00:00")
    from unittest.mock import patch
    from datetime import datetime, timezone
    with patch("app.services.journeys.datetime") as dt:
        dt.now.return_value = datetime(2026, 7, 1, 21, 0, tzinfo=timezone.utc)
        assert client.get(url).status_code == 404


def test_collector_trip_id_short_circuits_matching(client):
    """Wrong-town matching regression (one operator, same line number in
    two towns): when the collector's own trip_id is passed, the endpoint
    must serve exactly that trip — no re-matching, even with a junk journey
    code and no other hints."""
    data = client.get(
        "/api/journey-schedule/garbage?tripId=T_OUT").get_json()
    assert [s["stop_code"] for s in data["stops"]] == ["0100A", "0100B", "0100C"]


def test_bad_trip_id_falls_through_to_404(client):
    assert client.get(
        "/api/journey-schedule/garbage?tripId=NOPE").status_code == 404


def test_by_route_now_finds_previous_service_day_after_midnight(app):
    import sqlite3
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.services.journeys import journey_schedule
    london = ZoneInfo("Europe/London")
    conn = sqlite3.connect(app.config["BBB"].timetable_db)
    conn.row_factory = sqlite3.Row
    data = journey_schedule(
        conn, "garbage", operator="FBRI", line="75",
        direction_ref="outbound",
        now_local=datetime(2026, 7, 2, 0, 15, tzinfo=london))
    conn.close()
    assert [stop["stop_code"] for stop in data["stops"]] == ["0100A", "0100C"]

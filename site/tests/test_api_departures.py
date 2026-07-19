from datetime import datetime, timezone

from unittest.mock import patch

# 21:01 UTC is 60 seconds after the fixture vehicles' updated_at and remains
# inside the 90-second freshness window.
# FBRI-36205: S3 sched 22:10 + 120 s delay = 22:12 -> ceil(11 min).
# FBRI-30052: S3 sched 22:12 - 120 s early = 22:10 -> ceil(9 min).
NOW = datetime(2026, 7, 1, 21, 1, 0, tzinfo=timezone.utc)


def get(client, code):
    with patch("app.services.departures.datetime") as dt:
        dt.now.return_value = NOW
        return client.get(f"/api/departures/{code}")


def test_departure_board_shape_and_eta(client):
    data = get(client, "0100C").get_json()
    # cleaner corrections are keyed to real stop codes; fixture code 0100C
    # is not curated, so the raw name passes through.
    assert data["stop_name"] == "Hengrove Leisure Pk"
    lines = {d["vehicleRef"]: d for d in data["departures"]}
    # the mid-route bus: ETA = 22:10 sched + 2 min delay = 22:12 -> 10 min
    d = lines["FBRI-36205"]
    assert d["eta_mins"] == 11 and d["source"] == "live"
    assert d["line"] == "75" and d["destination"] == "Cribbs Causeway"
    assert d["current_stop"] == "Middle Stop"
    # the waiting-at-origin bus also serves S3 (seq 2 > its seq 1):
    # 22:12 sched - 2 min early = 22:10 -> 8 min
    assert lines["FBRI-30052"]["eta_mins"] == 9
    # sorted by eta: earliest first
    assert data["departures"][0]["vehicleRef"] == "FBRI-30052"


def test_bus_already_past_stop_not_shown(client):
    # For stop 0100B (seq 2), FBRI-36205 is AT seq 2 -> not "> seq" -> hidden;
    # FBRI-30052's trip T_OUT2 doesn't call at 0100B at all.
    data = get(client, "0100B").get_json()
    assert data["departures"] == []


def test_unknown_stop_404(client):
    assert get(client, "nope").status_code == 404


def test_stop_name_cleaner_curated_codes():
    from app.services.stop_names import clean_stop_name
    assert clean_stop_name("Hengrove Leisure Pk", "bstgpmt") == "Hengrove Leisure Park"
    assert clean_stop_name("Post Office", "bthadgp") == "Bath Union Street Post Office"
    assert clean_stop_name("Cabot Circus", "anything") == "Cabot Circus"

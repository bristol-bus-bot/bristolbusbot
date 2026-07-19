from datetime import datetime, timezone

from unittest.mock import patch

NOW = datetime(2026, 7, 1, 21, 0, 30, tzinfo=timezone.utc)


def get_buses(client):
    with patch("app.services.buses.datetime") as dt:
        dt.now.return_value = NOW
        return client.get("/api/buses").get_json()


def test_shape_and_staleness(client):
    data = get_buses(client)
    refs = {b["vehicleRef"] for b in data["buses"]}
    assert "FBRI-36205" in refs and "FBRI-DEPOT" in refs
    assert "FBRI-OLD" not in refs          # stale rows age out at read time
    assert data["count"] == len(data["buses"]) == 3


def test_frontend_fields(client):
    bus = next(b for b in get_buses(client)["buses"]
               if b["vehicleRef"] == "FBRI-36205")
    # Fields consumed by the frontend.
    for key in ("vehicleRef", "operatorRef", "line", "destination", "latitude",
                "longitude", "delayMinutes", "eventType", "waitingAtOrigin",
                "directionId", "journeyCode", "directionRef", "originAimedDep",
                "hasSchedule", "livery", "model", "fleetNumber", "reg",
                "lastStopName", "bearing", "description", "isElectric"):
        assert key in bus, f"missing {key}"
    assert bus["delayMinutes"] == 2 and bus["eventType"] == "punctual"
    assert bus["model"] == "Yutong E12" and bus["isElectric"] is True
    assert bus["lastStopName"] == "Middle Stop"     # code swapped for GTFS name
    assert bus["livery"]["left"] == "#e63946"


def test_waiting_at_origin(client):
    bus = next(b for b in get_buses(client)["buses"]
               if b["vehicleRef"] == "FBRI-30052")
    assert bus["waitingAtOrigin"] is True and bus["eventType"] == "waiting"


def test_depot_detection(client):
    bus = next(b for b in get_buses(client)["buses"]
               if b["vehicleRef"] == "FBRI-DEPOT")
    assert bus.get("atDepot") is True and bus["depotName"] == "Hengrove"
    assert bus["eventType"] == "depot"   # frontend needs this to grey + depot-icon it


def test_state_specific_blurbs(client):
    buses = {b["vehicleRef"]: b for b in get_buses(client)["buses"]}
    assert buses["FBRI-36205"]["description"] == "a fine electric bus"
    assert buses["FBRI-30052"]["description"] == "limbering up to depart"
    # depot bus: fleet ref FBRI-DEPOT -> fleet_number "DEPOT" in fixture
    assert buses["FBRI-DEPOT"]["description"] == "fast asleep at the shed"


def test_health_endpoints(client):
    assert client.get("/livez").status_code == 200
    h = client.get("/healthz").get_json()
    assert h["checks"]["gtfs_db"] == "ok"


def test_eurocoaches_yard_detected():
    from app.services.depots import check_depot
    # centroid of the surveyed yard outline
    assert check_depot(51.45592, -2.56926) == "Eurocoaches Yard"
    # edge of the yard, still inside the buffered circle
    assert check_depot(51.455401562115725, -2.56966093589615) == "Eurocoaches Yard"
    # Bedminster Parade, ~400 m away: not a depot
    assert check_depot(51.4520, -2.5900) is None


def test_stale_recorded_position_is_hidden(app, client):
    """A vehicle whose position was RECORDED >10 min ago must not render,
    even though the collector's upserts keep updated_at fresh (the
    frozen-city-centre ghost bug)."""
    from datetime import timedelta
    from conftest import NOW, _vehicle
    import sqlite3
    conn = sqlite3.connect(app.config["BBB"].live_db)
    from datetime import datetime as _dt, timezone as _tz
    frozen = _dt(2026, 7, 1, 21, 0, 30, tzinfo=_tz.utc)  # get_buses's NOW
    _vehicle(conn, ref="FBRI-GHOST", line="3",
             recorded=frozen - timedelta(minutes=20), updated=frozen)
    conn.commit(); conn.close()
    refs = [b["vehicleRef"] for b in get_buses(client)["buses"]]
    assert "FBRI-GHOST" not in refs
    assert "FBRI-36205" in refs


def test_conditional_get_returns_304(client):
    with patch("app.services.buses.datetime") as dt:
        dt.now.return_value = NOW
        first = client.get("/api/buses")
        etag = first.headers.get("ETag")
        assert etag
        again = client.get("/api/buses", headers={"If-None-Match": etag})
        assert again.status_code == 304
        assert again.get_data() == b""

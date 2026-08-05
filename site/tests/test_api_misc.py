import json
from datetime import datetime, timezone


def test_fleet_search_payload_from_list_shaped_file(client, app, tmp_path):
    # the REAL fbribuses.json is a list; verify list handling end to end
    fleet_file = tmp_path / "fleet_list.json"
    fleet_file.write_text(json.dumps([
        {"id": 1, "fleet_code": "36205", "fleet_number": 36205,
         "reg": "YX23 ABC", "vehicle_type": {"name": "Yutong E12"},
         "livery": {"name": "First", "left": "#e63946"},
         "special_features": "USB-A,USB-C"},
    ]))
    from app.services.fleet import Fleet
    app.extensions["bbb_fleet"] = Fleet(str(fleet_file))
    app.extensions.get("bbb_cache", {}).pop("fleet_search", None)
    data = client.get("/api/fleet").get_json()
    assert len(data["fleet"]) == 1                       # deduped across indexes
    v = data["fleet"][0]
    assert v["reg"] == "YX23 ABC".upper()
    assert v["special_features"] == ["USB-A", "USB-C"]   # string -> list
    # and vehicle-ref lookup works through both indexes
    f = app.extensions["bbb_fleet"]
    assert f.details("FBRI-36205")["model"] == "Yutong E12"
    assert f.details("YX23_ABC")["model"] == "Yutong E12"


def test_fleet_lookup_prefers_operator_and_suppresses_ambiguous_legacy_blurbs(
        tmp_path):
    fleet_file = tmp_path / "fleet.json"
    fleet_file.write_text(json.dumps([
        {"fleet_code": "101", "reg": "AA11 AAA",
         "operator": {"id": "OPAA"},
         "vehicle_type": {"name": "Operator A model"},
         "livery": {"name": "Operator A", "left": "#aa0000"}},
        {"fleet_code": "101", "reg": "BB11 BBB",
         "operator": {"id": "OPBB"},
         "vehicle_type": {"name": "Operator B model"},
         "livery": {"name": "Operator B", "left": "#0000bb"}},
    ]), encoding="utf-8")
    descriptions = tmp_path / "descriptions.json"
    descriptions.write_text(json.dumps({
        "101": "Ambiguous old description",
        "OPAA:101": "Scoped operator A description",
    }), encoding="utf-8")

    from app.services.fleet import Fleet
    fleet = Fleet(str(fleet_file), str(descriptions))

    assert fleet.details("OPAA-101", "OPAA")["model"] == "Operator A model"
    assert fleet.details("OPBB-101", "OPBB")["model"] == "Operator B model"
    assert fleet.details("OPAA-101", "OPAA")["reg"] == "AA11 AAA"
    assert fleet.description("101", operator_ref="OPAA") == \
        "Scoped operator A description"
    assert fleet.description("101", operator_ref="OPBB") is None


def test_same_operator_reused_code_requires_registration(tmp_path):
    fleet_file = tmp_path / "fleet.json"
    fleet_file.write_text(json.dumps([
        {"fleet_code": "303", "reg": "AA30 AAA",
         "operator": {"id": "OPAA"},
         "vehicle_type": {"name": "First vehicle"}},
        {"fleet_code": "303", "reg": "AA30 BBB",
         "operator": {"id": "OPAA"},
         "vehicle_type": {"name": "Second vehicle"}},
    ]), encoding="utf-8")

    from app.services.fleet import Fleet
    fleet = Fleet(str(fleet_file))

    assert fleet.details("OPAA-303", "OPAA")["model"] is None
    assert fleet.details("AA30_AAA", "OPAA")["model"] == "First vehicle"


def test_registration_collision_requires_operator(tmp_path):
    fleet_file = tmp_path / "fleet.json"
    fleet_file.write_text(json.dumps([
        {"fleet_code": "401", "reg": "ZZ40 ZZZ",
         "operator": {"id": "OPAA"},
         "vehicle_type": {"name": "Operator A vehicle"}},
        {"fleet_code": "402", "reg": "ZZ40 ZZZ",
         "operator": {"id": "OPBB"},
         "vehicle_type": {"name": "Operator B vehicle"}},
    ]), encoding="utf-8")

    from app.services.fleet import Fleet
    fleet = Fleet(str(fleet_file))

    assert fleet.details("ZZ40ZZZ")["model"] is None
    assert fleet.details("ZZ40ZZZ", "OPAA")["model"] == "Operator A vehicle"
    assert fleet.details("ZZ40ZZZ", "OPBB")["model"] == "Operator B vehicle"


def test_situations_endpoint(client, app):
    import sqlite3
    cfg = app.config["BBB"]
    conn = sqlite3.connect(cfg.live_db)
    conn.execute(
        """INSERT INTO situations (situation_number, version, participant,
               progress, planned, reason, summary, description, advice,
               severity, validity_start, validity_end, versioned_at, link,
               affected_json, closed_at, updated_at)
           VALUES ('sit-1', 1, 'WestofEngland', 'open', 1, 'roadworks',
                   'York Road closed', 'desc', 'advice', 'normal',
                   '2026-07-01T08:00:00Z', NULL, '2026-07-01T08:00:00Z', NULL,
                   '{"lines":[{"operator":"FBRI","line":"75","direction":""}],"stops":[],"operators":["FBRI"]}',
                   NULL, '2026-07-01T21:00:00Z')""")
    conn.execute("""INSERT INTO situations (situation_number, version,
               participant, progress, planned, reason, summary, description,
               advice, severity, validity_start, validity_end, versioned_at,
               link, affected_json, closed_at, updated_at)
           VALUES ('sit-closed', 1, 'WestofEngland', 'open', 1, 'roadworks',
                   'Old thing', '', '', '', NULL, NULL, NULL, NULL, '{}',
                   '2026-07-01T20:00:00Z', '2026-07-01T20:00:00Z')""")
    conn.commit(); conn.close()
    data = client.get("/api/situations").get_json()
    assert data["count"] == 1                            # closed one excluded
    s = data["situations"][0]
    assert s["summary"] == "York Road closed"
    assert s["affected"]["lines"][0]["line"] == "75"


def test_busbot_posts_shape(client):
    data = client.get("/api/busbot-posts").get_json()
    assert set(data) == {"posts", "profileUrl", "handle"}


def test_busbot_posts_matches_only_exact_current_journey(client, monkeypatch):
    from app.routes import api_misc
    current = {
        "operatorRef": "FBRI", "vehicleRef": "FBRI-36205",
        "journeyCode": "2100", "originAimedDep": "2026-07-01T21:00:00+00:00",
    }
    base = {
        "eventId": 42, "operatorRef": "FBRI", "vehicleRef": "FBRI-36205",
        "journeyRef": "2100", "originAimedDeparture": "2026-07-01T21:00:00+00:00",
        "line": "75", "postUrl": "https://bsky.app/profile/bristolbusbot.live/post/abc",
        "postText": "The exact published post.", "postType": "delay",
        "timestamp": "2026-07-01T21:02:00+00:00",
    }
    wrong_journey = {**base, "eventId": 43, "journeyRef": "WRONG"}
    matched = api_misc._match_bot_posts(
        [base, wrong_journey], [current], 240,
        now=datetime(2026, 7, 1, 21, 5, tzinfo=timezone.utc))
    assert len(matched) == 1
    assert matched[0]["eventId"] == 42
    assert matched[0]["postText"] == "The exact published post."


def test_busbot_posts_rejects_stale_missing_provenance_and_bad_url():
    from app.routes import api_misc
    current = {
        "operatorRef": "FBRI", "vehicleRef": "FBRI-36205",
        "journeyCode": "2100", "originAimedDep": "21:00:00",
    }
    base = {
        "operatorRef": "FBRI", "vehicleRef": "FBRI-36205",
        "journeyRef": "2100", "originAimedDeparture": "21:00:00",
        "postUrl": "https://bsky.app/profile/bristolbusbot.live/post/abc",
        "postText": "Post", "timestamp": "2026-07-01T20:00:00+00:00",
    }
    now = datetime(2026, 7, 1, 21, 0, tzinfo=timezone.utc)
    assert api_misc._match_bot_posts([{**base, "timestamp": "2026-07-01T12:00:00Z"}], [current], 240, now) == []
    assert api_misc._match_bot_posts([{**base, "journeyRef": ""}], [current], 240, now) == []
    assert api_misc._match_bot_posts([{**base, "postUrl": "https://example.com/x"}], [current], 240, now) == []


def test_busbot_posts_endpoint_fails_soft(client, monkeypatch):
    from app.routes import api_misc
    monkeypatch.setattr(api_misc, "_fetch_bot_posts",
                        lambda _url: (_ for _ in ()).throw(OSError("bot down")))
    response = client.get("/api/busbot-posts")
    assert response.status_code == 200
    assert response.get_json()["posts"] == []
    assert response.headers["Cache-Control"] == "no-store"


def test_boundary_serves_real_file_then_404_when_missing(client, app):
    # default config points at the repo's real boundary GeoJSON
    r = client.get("/api/boundary")
    assert r.status_code == 200 and r.get_json()["type"] in ("FeatureCollection", "Feature")
    # and a missing file is a 404, not a 500
    app.config["BBB"].boundary_geojson = "/nowhere/nothing.geojson"
    app.extensions["bbb_cache"].pop("boundary", None)
    assert client.get("/api/boundary").status_code == 404


def test_index_serves_frontend(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"map" in r.data.lower()


def test_stops_with_locality_shape(client):
    data = client.get("/api/stops-with-locality").get_json()
    s = {x["stop_code"]: x for x in data["stops"]}
    assert s["0100C"]["routes"] == ["75"]
    for key in ("ward", "area", "street", "enriched_locality", "local_authority"):
        assert key in s["0100A"]


def test_stops_with_locality_uses_precomputed_routes_without_schedule_joins(
        client, app):
    import sqlite3

    cfg = app.config["BBB"]
    connection = sqlite3.connect(cfg.timetable_db)
    connection.execute("DROP TABLE stop_times")
    connection.execute("DROP TABLE trips")
    connection.execute("DROP TABLE routes")
    connection.commit()
    connection.close()

    data = client.get("/api/stops-with-locality").get_json()
    stops = {item["stop_code"]: item for item in data["stops"]}
    assert stops["0100C"]["routes"] == ["75"]


def test_stops_with_locality_keeps_legacy_rollback_compatibility(client, app):
    import sqlite3

    cfg = app.config["BBB"]
    connection = sqlite3.connect(cfg.timetable_db)
    connection.execute("DROP TABLE stop_routes")
    connection.commit()
    connection.close()

    data = client.get("/api/stops-with-locality").get_json()
    stops = {item["stop_code"]: item for item in data["stops"]}
    assert stops["0100C"]["routes"] == ["75"]

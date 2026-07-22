import json


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

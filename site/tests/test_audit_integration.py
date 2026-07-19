from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services.audit_integration import AuditIntegration


SLUG = "fbri-0123456789ab"


def install_snapshot(app, tmp_path, *, published_at=None, readings=45):
    path = tmp_path / "audit_integration.json"
    path.write_text(json.dumps({
        "schema": 1,
        "published_at": (published_at or datetime.now(timezone.utc)).isoformat(),
        "audit_url": "https://bristol-bus-bot.github.io/weca-bus-audit/",
        "headline": {
            "measurement_start": "20260714",
            "through_date": "20260716",
            "readings": readings,
            "on_time_pct": 55.0,
            "minimum_readings": 30,
            "eligible": readings >= 30,
        },
        "profiles": [{
            "slug": SLUG,
            "operator": "FBRI",
            "operator_name": "First Bristol",
            "vehicle_ref": "FBRI-36205",
            "measurement_start": "20260714",
            "through_date": "20260716",
            "observed_days": 3,
            "readings": 45,
            "on_time": 30,
            "early": 5,
            "late": 10,
            "on_time_pct": 66.7,
            "routes": [{"route": "75", "observed_days": 3, "readings": 45}],
        }],
        "rare_workings": {"mode": "shadow", "events": []},
    }), encoding="utf-8")
    app.extensions["bbb_audit_integration"] = AuditIntegration(str(path))
    return path


def test_compact_headline_and_vehicle_links_use_fresh_published_snapshot(
        app, client, tmp_path):
    install_snapshot(app, tmp_path)

    index = client.get("/")
    assert index.status_code == 200
    assert b"Audit: 55.0% on time" in index.data
    assert b"Full methodology" not in index.data
    header_start = index.data.index(b"<header")
    header_end = index.data.index(b"</header>", header_start)
    audit_link = index.data.index(b'id="audit-headline-link"')
    bluesky_link = index.data.index(b'id="bsky-link"')
    assert header_start < audit_link < bluesky_link < header_end

    now = datetime(2026, 7, 1, 21, 0, 30, tzinfo=timezone.utc)
    with patch("app.services.buses.datetime") as mocked:
        mocked.now.return_value = now
        buses = client.get("/api/buses").get_json()["buses"]
    bus = next(item for item in buses if item["vehicleRef"] == "FBRI-36205")
    assert bus["profileUrl"] == f"/vehicles/{SLUG}"

    profile = client.get(f"/vehicles/{SLUG}")
    assert profile.status_code == 200
    assert b"66.7%" in profile.data
    assert b"45" in profile.data
    assert b"Full audit and method" in profile.data


def test_fleet_search_gets_profile_link_from_unambiguous_fleet_code(
        app, client, tmp_path):
    install_snapshot(app, tmp_path)
    fleet_file = tmp_path / "fleet_list.json"
    fleet_file.write_text(json.dumps([{
        "id": 1, "fleet_code": "36205", "fleet_number": 36205,
        "reg": "YX23 ABC", "vehicle_type": {"name": "Yutong E12"},
    }]), encoding="utf-8")
    from app.services.fleet import Fleet
    app.extensions["bbb_fleet"] = Fleet(str(fleet_file))
    app.extensions.get("bbb_cache", {}).pop("fleet_search", None)

    vehicle = client.get("/api/fleet").get_json()["fleet"][0]
    assert vehicle["profile_url"] == f"/vehicles/{SLUG}"


def test_stale_or_small_snapshot_hides_every_public_surface(
        app, client, tmp_path):
    install_snapshot(
        app, tmp_path,
        published_at=datetime.now(timezone.utc) - timedelta(hours=49),
    )
    assert b"Audit: 55.0% on time" not in client.get("/").data
    assert client.get(f"/vehicles/{SLUG}").status_code == 404

    install_snapshot(app, tmp_path, readings=29)
    assert b"Audit: 55.0% on time" not in client.get("/").data
    # Headline and profile samples are independent; an eligible profile remains.
    assert client.get(f"/vehicles/{SLUG}").status_code == 200

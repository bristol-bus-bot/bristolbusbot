"""The busiest public endpoints may be cached at Cloudflare for 10 seconds."""
import pytest

EDGE = "public, max-age=0, s-maxage=10"


@pytest.mark.parametrize("path", ["/api/departures/0100B", "/api/scheduled-departures/0100B"])
def test_successful_board_answers_are_edge_cacheable(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == EDGE


@pytest.mark.parametrize("path", ["/api/departures/NOSUCHSTOP", "/api/scheduled-departures/NOSUCHSTOP"])
def test_unknown_stops_are_never_edge_cached(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert "s-maxage" not in response.headers.get("Cache-Control", "")


def test_buses_keep_their_etag_and_304_with_the_edge_header(client):
    first = client.get("/api/buses")
    assert first.status_code == 200
    assert first.headers["Cache-Control"] == EDGE
    etag = first.headers["ETag"]
    again = client.get("/api/buses", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["Cache-Control"] == EDGE


def test_other_api_answers_are_not_edge_cached(client):
    response = client.get("/api/notices/stops")
    assert "s-maxage" not in response.headers.get("Cache-Control", "")

from datetime import datetime, timezone

from app.services.map_timing import display_timing, project


def stamp(hour, minute):
    return datetime(2026, 9, 6, hour - 1, minute, tzinfo=timezone.utc)


def vehicle(hour=15, minute=12, **updates):
    row = dict(trip_id="trip", lat=51.5, lon=-2.5,
               recorded_at=stamp(hour, minute).isoformat(),
               origin_aimed_departure=stamp(15, 30).isoformat(),
               journey_ref="1530", delay_seconds=None)
    return row | updates


def stop(seq, dep, lat=51.5, lon=-2.5, arrival=None):
    return (seq, arrival or dep, dep, lat, lon, "Stop")


def test_captured_predeparture_case_supports_zero_based_and_circular_stops():
    row = vehicle()
    for first in (0, 1, 10):
        schedule = [stop(first, "15:30:00"), stop(first + 1, "16:06:00")]
        result = display_timing(row, schedule, [], stamp(15, 12))
        assert result["eventType"] == "waiting"
        assert result["delayMinutes"] is None
        assert result["waitingAtOrigin"] is True


def test_scheduled_twenty_minute_stopover_is_not_early_running():
    schedule = [stop(0, "12:30:00", 51.49, -.149),
                stop(1, "15:35:00", arrival="15:15:00"),
                stop(2, "16:35:00", 51.1, -3)]
    result = display_timing(vehicle(origin_aimed_departure=stamp(12, 30).isoformat()),
                            schedule, [], stamp(15, 12))
    assert result["eventType"] == "waiting"
    assert result["waitingAtOrigin"] is False
    assert result["waitingAtStop"] is True


def test_waiting_expires_and_does_not_cover_wrong_place_or_previous_lap():
    schedule = [stop(0, "15:30:00"), stop(1, "16:06:00")]
    assert display_timing(vehicle(), schedule, [], stamp(15, 31)) == {}
    assert display_timing(vehicle(lat=51.51), schedule, [], stamp(15, 12)) == {}
    assert display_timing(vehicle(16, 7), schedule, [], stamp(16, 7)) == {}


def test_progress_uses_previous_departure_and_next_arrival():
    schedule = [stop(0, "15:00:00", lon=-2.52),
                stop(1, "15:30:00", lon=-2.48, arrival="15:20:00")]
    row = vehicle(origin_aimed_departure=stamp(15, 0).isoformat())
    result = display_timing(row, schedule, [[[51.5, -2.52], [51.5, -2.48]]], stamp(15, 12))
    assert result["timingSource"] == "route_estimate"
    assert result["delayMinutes"] == 2


def test_progress_rejects_off_route_ambiguous_or_incompatible_shapes():
    schedule = [stop(0, "15:00:00", lon=-2.52), stop(1, "15:20:00", lon=-2.48)]
    row = vehicle(origin_aimed_departure=stamp(15, 0).isoformat())
    shape = [[51.5, -2.52], [51.5, -2.48]]
    assert display_timing(row | {"lat": 51.51}, schedule, [shape], stamp(15, 12)) == {}
    assert display_timing(row, schedule, [shape[::-1]], stamp(15, 12)) == {}
    assert project((51.5, -2.5), shape + shape[::-1]) is None


def test_estimate_never_replaces_existing_measurement_or_unmatched_bus():
    schedule = [stop(0, "15:00:00", lon=-2.52), stop(1, "15:20:00", lon=-2.48)]
    shape = [[51.5, -2.52], [51.5, -2.48]]
    for changes in ({"trip_id": None}, {"delay_seconds": 20}):
        assert display_timing(vehicle(**changes), schedule, [shape], stamp(15, 12)) == {}


def test_ambiguous_terminal_loop_does_not_invalidate_unique_earlier_section():
    schedule = [stop(0, "15:00:00", lon=-2.52),
                stop(1, "15:20:00", lon=-2.48), stop(2, "15:30:00", lon=-2.46)]
    shape = [[51.5, -2.52], [51.5, -2.48], [51.5, -2.46],
             [51.502, -2.46], [51.502, -2.458], [51.5, -2.46]]
    row = vehicle(origin_aimed_departure=stamp(15, 0).isoformat())
    assert display_timing(row, schedule, [shape], stamp(15, 12))["delayMinutes"] == 2


def test_stale_positions_and_implausible_times_remain_unavailable():
    schedule = [stop(0, "15:00:00", lon=-2.52), stop(1, "15:20:00", lon=-2.48)]
    shape = [[51.5, -2.52], [51.5, -2.48]]
    assert display_timing(vehicle(), schedule, [shape], stamp(15, 15)) == {}
    assert display_timing(vehicle(17, 0), schedule, [shape], stamp(17, 0)) == {}


def test_stopover_requires_a_real_dwell_and_unique_stop():
    schedule = [stop(0, "12:30:00", 51.49, -.149),
                stop(1, "15:35:00"), stop(2, "16:35:00", 51.1, -3)]
    assert display_timing(vehicle(), schedule, [], stamp(15, 12)) == {}


def test_real_api_waiting_keeps_null_delay_and_does_not_change_database(app, client):
    import sqlite3
    from unittest.mock import patch
    now = datetime(2026, 7, 1, 21, 0, 30, tzinfo=timezone.utc)
    with sqlite3.connect(app.config["BBB"].live_db) as conn:
        conn.execute("UPDATE vehicles SET delay_seconds=NULL WHERE vehicle_ref='FBRI-30052'")
    with patch("app.services.buses.datetime") as dt:
        dt.now.return_value = now
        bus = next(b for b in client.get('/api/buses').get_json()['buses']
                   if b['vehicleRef'] == 'FBRI-30052')
    assert bus['eventType'] == 'waiting' and bus['delayMinutes'] is None
    with sqlite3.connect(app.config['BBB'].live_db) as conn:
        assert conn.execute("SELECT delay_seconds FROM vehicles WHERE vehicle_ref='FBRI-30052'").fetchone()[0] is None

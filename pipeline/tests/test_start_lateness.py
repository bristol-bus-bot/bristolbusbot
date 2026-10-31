import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import start_lateness  # noqa: E402
from audit_operators import NETWORK_LABEL, public_route  # noqa: E402

DAYS = [f"202610{d:02d}" for d in range(1, 11)]


def database(route_delays):
    """route_delays: {route: {hour: delay_s}}; one bus per route, one trip per hour, every day."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE timepoint_observations (
        service_date TEXT, operator TEXT, route TEXT, trip_id TEXT, stop_sequence INTEGER,
        stop_code TEXT, scheduled_local TEXT, observed_delay_s INTEGER, gps_distance_m INTEGER,
        vehicle_ref TEXT, is_origin INTEGER DEFAULT 0)""")
    for day in DAYS:
        for route, by_hour in route_delays.items():
            for hour, delay in by_hour.items():
                trip = f"{route}-{day}-{hour}"
                stamp = f"{day[:4]}-{day[4:6]}-{day[6:]} {hour:02d}:05:00+01:00"
                # The origin reading (a bus waiting) is ignored; the next one counts.
                conn.execute("INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                             (day, "LEMB", route, trip, 1, "A", stamp, 900, 10, f"V{route}"))
                conn.execute("INSERT INTO timepoint_observations VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                             (day, "LEMB", route, trip, 2, "B", stamp, delay, 10, f"V{route}"))
    return conn


def test_a_route_that_snowballs_and_one_that_does_not():
    conn = database({
        "20": {7: 30, 8: 60, 9: 60, 13: 540, 15: 600, 17: 600},
        "K1": {7: 120, 8: 120, 9: 120, 13: 150, 15: 120, 17: 120},
    })
    figures = start_lateness.build(conn, DAYS, public_route, NETWORK_LABEL)
    snowball = figures[("LEMB", "20")]
    assert snowball == {"morning_min": 1.0, "later_min": 10.0, "later_days": 10, "bus_days": 10}
    flat = figures[("LEMB", "K1")]
    assert flat["later_days"] == 0 and flat["later_min"] - flat["morning_min"] < 1
    # The network view files it under the tagged label.
    assert (NETWORK_LABEL, "20 Big Lemon") in figures


def test_too_little_data_gives_no_figure():
    conn = database({"20": {7: 30, 13: 600}})
    assert start_lateness.build(conn, DAYS[:2], public_route, NETWORK_LABEL) == {}


def test_figures_attach_to_period_route_rows():
    summary = {"period": {"by_operator": {"LEMB": {"routes": [{"route": "20"}, {"route": "K1"}]}}}}
    added = start_lateness.attach(summary, {("LEMB", "20"): {"morning_min": 1.0}})
    assert added == 1
    assert summary["period"]["by_operator"]["LEMB"]["routes"][0]["start_lateness"] == {"morning_min": 1.0}

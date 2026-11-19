"""#137: excess waiting time, private rollup."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import excess_waiting  # noqa: E402

MIN = 60


def test_even_gaps_give_half_the_gap_and_bunching_makes_it_longer():
    even = [0, 10 * MIN, 20 * MIN, 30 * MIN]
    assert excess_waiting.waiting_minutes(even) == 5.0
    bunched = [0, 1 * MIN, 20 * MIN, 21 * MIN]          # same buses, two pairs
    assert excess_waiting.waiting_minutes(bunched) > 5.0
    assert excess_waiting.waiting_minutes([0]) is None


def test_a_stop_hour_is_kept_only_when_enough_buses_were_seen():
    hour = 8 * 3600
    scheduled = {("A1", "S1"): [hour + i * 10 * MIN for i in range(6)]}
    # all six seen, each two minutes late: no excess
    on_time = {("A1", "S1"): {s: s + 2 * MIN for s in scheduled[("A1", "S1")]}}
    [row] = excess_waiting.stop_hour_results(scheduled, on_time)
    assert row["seen_journeys"] == 6 and abs(row["actual_wait"] - row["scheduled_wait"]) < 1e-9
    # one bus not seen: kept at 80%, not at 100% or 90%
    missing = {("A1", "S1"): {s: s for s in scheduled[("A1", "S1")][:5]}}
    rows = excess_waiting.stop_hour_results(scheduled, missing)
    summary = excess_waiting.summarise(rows)
    assert summary["stop_hours"] == 1
    assert summary["tolerances"]["100% seen"]["kept_stop_hours"] == 0
    assert summary["tolerances"]["90% seen"]["kept_stop_hours"] == 0
    assert summary["tolerances"]["80% seen"]["kept_stop_hours"] == 1


def test_infrequent_hours_are_not_counted():
    scheduled = {("75", "S1"): [9 * 3600 + i * 20 * MIN for i in range(3)]}
    assert excess_waiting.stop_hour_results(scheduled, {}) == []

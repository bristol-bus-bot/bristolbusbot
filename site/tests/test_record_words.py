from app.services.record_words import route_comparison, summary


def profile(on_time, others=None, hours=None):
    return {"on_time_pct": on_time,
            "comparison": None if others is None else {"others_on_time_pct": others, "compared_readings": 400},
            "hours": hours or {}}


def test_about_the_same_puts_it_on_the_timetable_not_the_driver():
    lines = summary(profile(61.8, 60.2))
    assert lines[0] == "This bus was on time for 62% of the timing points we measured it at."
    assert "were on time 60% of the time" in lines[1]
    assert lines[2].startswith("So it did about as well as the buses around it.")
    assert "not the person driving" in lines[2]


def test_better_and_worse():
    assert summary(profile(75, 60))[2] == "So it did better than the buses around it."
    worse = summary(profile(50, 62))[2]
    assert worse.startswith("So it did worse") and "isn't a verdict on its drivers" in worse


def test_hours_line_needs_two_solid_hours_and_a_real_difference():
    hours = {"8": [30, 0, 70], "11": [85, 5, 10], "22": [1, 0, 1]}
    assert summary(profile(60, 60, hours))[-1] == "It ran best around 11:00 (85% on time) and worst around 08:00 (30%)."
    flat = {"8": [70, 0, 30], "11": [75, 0, 25]}
    assert not any("ran best" in line for line in summary(profile(60, 60, flat)))


def test_missing_data_says_less_rather_than_guessing():
    assert summary({"on_time_pct": None}) == []
    assert len(summary(profile(60))) == 1
    assert route_comparison({"comparison": None}) is None
    assert route_comparison({"comparison": {"others_on_time_pct": 58.4}}) == "other buses at the same times 58%"

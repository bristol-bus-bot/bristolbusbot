"""The audit's on-time rule, in one place.

A reading is on time between 1 minute early and 5 minutes 59 seconds late.
At the last timing point of a trip, arriving early is not a failure: the
Senior Traffic Commissioner's Statutory Document No. 14 (v6.0, paras 43-45)
checks final destination points only against late arrival. So a final-stop
reading more than a minute early counts as on time; late is still late.
"""
from __future__ import annotations

ON_TIME_LOW_S = -60
ON_TIME_HIGH_S = 359
# The first service day whose retained readings can say which timing point
# ended the trip (scheduled trips record last_departure from this day).
FINAL_STOP_RULE_FROM = "20260822"


def final_early(delay_s: int, is_final) -> bool:
    """True when the reading is on time only because of the final-stop rule."""
    return bool(is_final) and delay_s < ON_TIME_LOW_S


def delay_band(delay_s: int, is_final=False) -> str:
    if delay_s > ON_TIME_HIGH_S:
        return "late"
    if delay_s < ON_TIME_LOW_S and not is_final:
        return "early"
    return "on_time"


def is_on_time(delay_s: int, is_final=False) -> bool:
    return delay_band(delay_s, is_final) == "on_time"


def delay_bucket(delay_s: int, is_final=False) -> str:
    if delay_s < ON_TIME_LOW_S and is_final:
        return "on_time"
    if delay_s < -300:
        return "early_5plus"
    if delay_s < ON_TIME_LOW_S:
        return "early_1_5"
    if delay_s <= ON_TIME_HIGH_S:
        return "on_time"
    if delay_s <= 600:
        return "late_6_10"
    if delay_s <= 1200:
        return "late_10_20"
    return "late_20plus"


def on_time_sql(delay: str = "observed_delay_s", final: str = "is_final") -> str:
    """SQL boolean for the same rule. ``final`` may be the literal NULL."""
    return (f"({delay} BETWEEN {ON_TIME_LOW_S} AND {ON_TIME_HIGH_S} "
            f"OR (COALESCE({final}, 0) = 1 AND {delay} < {ON_TIME_LOW_S}))")


def early_sql(delay: str = "observed_delay_s", final: str = "is_final") -> str:
    return f"({delay} < {ON_TIME_LOW_S} AND COALESCE({final}, 0) = 0)"

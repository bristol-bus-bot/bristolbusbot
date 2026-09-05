#!/usr/bin/env python3
"""Build one bounded private research archive from the collector audit DB.

The source is opened read-only and copied with SQLite's online backup API.
Only explicitly listed tables and columns leave that private snapshot.  The
result is a ZIP containing a typed SQLite database and a plain-English README.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
import zipfile
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

try:
    import fcntl
except ModuleNotFoundError:  # Windows imports this module for local verification.
    fcntl = None  # type: ignore[assignment]


SCHEMA_VERSION = 1
DEFAULT_AUDIT_DB = Path("/var/lib/bristolbusbot/collector/audit.db")
DEFAULT_EXPORT_ROOT = Path("/var/lib/bristolbusbot/private-exports")
DEFAULT_LOCK = Path("/run/lock/bristolbusbot/heavy-io.lock")
DATABASE_MEMBER = "collector-research.sqlite"
README_MEMBER = "README.txt"
MAX_DATABASE_BYTES = 768 * 1024 * 1024
MAX_ARCHIVE_BYTES = 160 * 1024 * 1024
MIN_FREE_BYTES = 4 * 1024 * 1024 * 1024
LOCK_WAIT_SECONDS = 15 * 60
REQUEST_ID_RE = re.compile(r"[a-f0-9]{12}")
ARCHIVE_NAME_RE = re.compile(
    r"collector-research-(\d{8})-to-(\d{8})-([a-f0-9]{12})\.zip")
LONDON = ZoneInfo("Europe/London")


class ResearchExportError(RuntimeError):
    """An expected, safe reason the private export cannot be produced."""


@dataclass(frozen=True)
class Column:
    name: str
    declared_type: str


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[Column, ...]
    date_expression: str
    order_by: tuple[str, ...]
    role: str
    description: str
    required: bool = False
    hyphenated_date: bool = False


def columns(definition: str) -> tuple[Column, ...]:
    result = []
    for token in definition.split():
        name, declared_type = token.split(":", 1)
        result.append(Column(name, declared_type))
    return tuple(result)


TABLE_SPECS = (
    TableSpec(
        "timepoint_observations",
        columns("service_date:TEXT operator:TEXT route:TEXT trip_id:TEXT "
                "siri_journey_ref:TEXT stop_sequence:INTEGER stop_code:TEXT "
                "scheduled_local:TEXT observed_delay_s:INTEGER on_time:INTEGER "
                "gps_distance_m:INTEGER recorded_at:TEXT vehicle_ref:TEXT "
                "is_origin:INTEGER match_tier:TEXT"),
        "service_date", ("service_date", "trip_id", "stop_sequence"),
        "baseline",
        "One closest retained timing-point reading per matched trip and stop.",
        required=True,
    ),
    TableSpec(
        "poll_log",
        columns("poll_at:TEXT ok:INTEGER vehicles_total:INTEGER candidates:INTEGER "
                "matched:INTEGER obs_written:INTEGER dropped_insane:INTEGER "
                "stale:INTEGER evidence_written:INTEGER evidence_dropped:INTEGER "
                "evidence_deduplicated:INTEGER evidence_scope_dropped:INTEGER "
                "evidence_quota_dropped:INTEGER evidence_errors:INTEGER"),
        "substr(poll_at,1,10)", ("poll_at",), "collection_context",
        "One collector-poll health row, normally about every 30 seconds.",
        required=True, hyphenated_date=True,
    ),
    TableSpec(
        "expected_trips",
        columns("service_date:TEXT operator:TEXT route:TEXT trip_id:TEXT "
                "siri_ref:TEXT direction:INTEGER first_departure:TEXT route_id:TEXT "
                "service_id:TEXT block_id:TEXT vehicle_journey_code:TEXT "
                "first_stop_id:TEXT first_stop_code:TEXT timetable_edition:TEXT "
                "last_departure:TEXT"),
        "service_date", ("service_date", "trip_id"), "timetable_context_untrusted",
        "Daily scheduled-trip snapshot; known to contain unstable denominators.",
        required=True,
    ),
    TableSpec(
        "matching_evidence",
        columns("evidence_id:TEXT captured_at:TEXT service_date:TEXT operator:TEXT "
                "sampling_date:TEXT sampling_band:TEXT sampling_reason:TEXT "
                "route:TEXT vehicle_ref:TEXT direction:TEXT journey_ref:TEXT "
                "origin_aimed_departure:TEXT origin_ref:TEXT destination_ref:TEXT "
                "recorded_at:TEXT bearing:REAL "
                "block_ref:TEXT chosen_trip_id:TEXT match_tier:TEXT "
                "candidate_count:INTEGER candidates_truncated:INTEGER "
                "gps_distance_m:INTEGER delay_s:INTEGER event_type:TEXT "
                "timetable_route_id:TEXT timetable_service_id:TEXT "
                "timetable_direction_id:INTEGER timetable_edition:TEXT"),
        "service_date", ("captured_at", "evidence_id"),
        "rule_selected_comparison_group",
        "Bounded receipts selected by existing anomaly rules; not a baseline sample.",
        required=True,
    ),
    TableSpec(
        "rare_working_evidence",
        columns("event_id:TEXT evaluated_at:TEXT service_date:TEXT operator:TEXT "
                "vehicle_ref:TEXT route:TEXT profile_slug:TEXT vehicle_days:INTEGER "
                "route_days:INTEGER pair_days:INTEGER recent_pair_days:INTEGER "
                "candidate_readings:INTEGER candidate_points:INTEGER queued:INTEGER"),
        "service_date", ("service_date", "event_id"),
        "rule_selected_comparison_group",
        "Derived rare vehicle-and-route working candidates; not a baseline sample.",
    ),
    TableSpec(
        "daily_overall_summary",
        columns("service_date:TEXT operator:TEXT readings_in_gate:INTEGER "
                "on_time:INTEGER early:INTEGER late:INTEGER on_time_pct:REAL "
                "mean_delay_s:INTEGER median_delay_s:INTEGER readings_total:INTEGER "
                "excluded_distance:INTEGER median_gate_dist_m:INTEGER "
                "expected_trips:INTEGER observed_trips:INTEGER coverage_pct:REAL"),
        "service_date", ("service_date", "operator"), "permanent_rollup",
        "Daily pooled and per-operator punctuality summary.",
    ),
    TableSpec(
        "daily_route_summary",
        columns("service_date:TEXT operator:TEXT route:TEXT readings_in_gate:INTEGER "
                "on_time:INTEGER early:INTEGER late:INTEGER on_time_pct:REAL "
                "mean_delay_s:INTEGER median_delay_s:INTEGER readings_total:INTEGER "
                "excluded_distance:INTEGER median_gate_dist_m:INTEGER "
                "expected_trips:INTEGER observed_trips:INTEGER coverage_pct:REAL"),
        "service_date", ("service_date", "operator", "route"), "permanent_rollup",
        "Daily punctuality and coverage summary by route.",
    ),
    TableSpec(
        "daily_delay_histogram",
        columns("service_date:TEXT operator:TEXT route:TEXT bucket:TEXT n:INTEGER"),
        "service_date", ("service_date", "operator", "route", "bucket"),
        "permanent_rollup", "Daily counts in fixed delay buckets by route.",
    ),
    TableSpec(
        "daily_peak_summary",
        columns("service_date:TEXT operator:TEXT route:TEXT peak_band:TEXT "
                "readings_in_gate:INTEGER on_time:INTEGER early:INTEGER late:INTEGER "
                "on_time_pct:REAL mean_delay_s:INTEGER median_delay_s:INTEGER"),
        "service_date", ("service_date", "operator", "route", "peak_band"),
        "permanent_rollup", "Daily punctuality summary by route and time band.",
    ),
    TableSpec(
        "daily_geo_summary",
        columns("service_date:TEXT operator:TEXT geo_type:TEXT geo_key:TEXT "
                "readings_in_gate:INTEGER on_time:INTEGER on_time_pct:REAL "
                "mean_delay_s:INTEGER median_delay_s:INTEGER"),
        "service_date", ("service_date", "operator", "geo_type", "geo_key"),
        "permanent_rollup", "Daily punctuality summary by locality or ward.",
    ),
    TableSpec(
        "daily_geo_route_summary",
        columns("service_date:TEXT operator:TEXT source_operator:TEXT geo_type:TEXT "
                "geo_key:TEXT route:TEXT readings_in_gate:INTEGER on_time:INTEGER "
                "early:INTEGER late:INTEGER on_time_pct:REAL mean_delay_s:INTEGER "
                "median_delay_s:INTEGER"),
        "service_date",
        ("service_date", "operator", "source_operator", "geo_type", "geo_key", "route"),
        "permanent_rollup", "Daily locality-or-ward figures split by route.",
    ),
    TableSpec(
        "daily_fleet_summary",
        columns("service_date:TEXT operator:TEXT model:TEXT electric:INTEGER fuel:TEXT "
                "vehicles:INTEGER readings_in_gate:INTEGER on_time:INTEGER "
                "on_time_pct:REAL mean_delay_s:INTEGER median_delay_s:INTEGER"),
        "service_date", ("service_date", "operator", "model"), "permanent_rollup",
        "Daily punctuality summary by known vehicle model; routes are normalised separately.",
    ),
    TableSpec(
        "daily_route_class",
        columns("service_date:TEXT operator:TEXT route:TEXT frequent:INTEGER "
                "peak_hourly:INTEGER"),
        "service_date", ("service_date", "operator", "route"), "permanent_rollup",
        "Daily route-frequency classification using the six-per-hour threshold.",
    ),
    TableSpec(
        "daily_trip_coverage_days",
        columns("service_date:TEXT is_valid:INTEGER poll_window_start:TEXT "
                "poll_window_end:TEXT expected_polls:INTEGER recorded_polls:INTEGER "
                "successful_polls:INTEGER successful_poll_rate_pct:REAL "
                "successful_poll_coverage_pct:REAL max_successful_poll_gap_s:INTEGER "
                "candidate_readings:INTEGER matched_readings:INTEGER match_rate_pct:REAL "
                "scheduled_trips:INTEGER observed_trips:INTEGER unobserved_trips:INTEGER "
                "exact_observed_trips:INTEGER fuzzy_observed_trips:INTEGER "
                "unknown_tier_observed_trips:INTEGER invalid_departure_times:INTEGER"),
        "service_date", ("service_date",), "coverage_health_rollup",
        "Daily collection-health gate for expected-versus-observed trip analysis.",
    ),
    TableSpec(
        "daily_trip_coverage",
        columns("service_date:TEXT operator:TEXT route:TEXT direction:INTEGER "
                "time_band:TEXT scheduled_trips:INTEGER observed_trips:INTEGER "
                "unobserved_trips:INTEGER exact_observed_trips:INTEGER "
                "fuzzy_observed_trips:INTEGER unknown_tier_observed_trips:INTEGER"),
        "service_date", ("service_date", "operator", "route", "direction", "time_band"),
        "coverage_rollup_untrusted_denominator",
        "Expected-versus-observed trip counts; use only where the day health row is valid, and treat the expected denominator as under investigation.",
    ),
    TableSpec(
        "daily_duty_gap_days",
        columns("service_date:TEXT operator:TEXT is_valid:INTEGER "
                "scheduled_trips:INTEGER duty_detail_trips:INTEGER duty_detail_pct:REAL "
                "scheduled_blocks:INTEGER usable_blocks:INTEGER "
                "missing_middle_trips:INTEGER same_vehicle_gaps:INTEGER "
                "short_connection_gaps:INTEGER ambiguous_match_gaps:INTEGER "
                "strict_candidate_gaps:INTEGER"),
        "service_date", ("service_date", "operator"), "vehicle_duty_gap_rollup",
        "Daily validity and counts for cautious vehicle-duty gap candidates.",
    ),
    TableSpec(
        "daily_duty_gap_candidates",
        columns("service_date:TEXT operator:TEXT block_id:TEXT trip_id:TEXT route:TEXT "
                "direction:INTEGER first_departure:TEXT last_departure:TEXT "
                "vehicle_ref:TEXT previous_trip_id:TEXT previous_route:TEXT "
                "previous_departure:TEXT previous_siri_ref:TEXT next_trip_id:TEXT "
                "next_route:TEXT next_departure:TEXT next_siri_ref:TEXT "
                "connection_before_s:INTEGER connection_after_s:INTEGER "
                "previous_match_window:INTEGER next_match_window:INTEGER"),
        "service_date", ("service_date", "operator", "trip_id"),
        "vehicle_duty_gap_candidates_not_cancellations",
        "Strict missing-middle candidates within a broadcasting vehicle duty; not proof of cancellation.",
    ),
)


COMMON_DOCS = {
    "service_date": ("YYYYMMDD", "Local operating day in Europe/London."),
    "operator": ("code", "Operator National Operator Code from the public feed."),
    "route": ("text", "Public route or line label."),
    "trip_id": ("identifier", "Matched timetable trip identifier."),
    "siri_journey_ref": ("identifier", "Journey reference supplied by SIRI-VM."),
    "stop_sequence": ("ordinal", "Position of the timing point within the timetable trip."),
    "stop_code": ("identifier", "Public stop code for the matched timing point."),
    "scheduled_local": ("ISO local datetime", "Scheduled timing-point time in Europe/London."),
    "observed_delay_s": ("seconds", "Recorded time minus scheduled time; negative means early."),
    "on_time": ("boolean 0/1", "Whether the stored reading was from 60 seconds early through 359 seconds late."),
    "gps_distance_m": ("metres", "Distance between reported vehicle position and matched stop."),
    "recorded_at": ("ISO datetime", "Vehicle timestamp supplied by the public feed."),
    "vehicle_ref": ("identifier", "Vehicle reference supplied by the public feed."),
    "is_origin": ("boolean 0/1", "Whether this is the trip's first timing point; public method excludes these rows."),
    "match_tier": ("category", "Journey match type such as exact or fuzzy."),
    "poll_at": ("ISO UTC datetime", "Time the collector poll was recorded."),
    "ok": ("boolean 0/1", "Whether the collector poll completed successfully."),
    "vehicles_total": ("count", "Vehicles present in the source snapshot."),
    "candidates": ("count", "Vehicle readings considered for timetable matching."),
    "matched": ("count", "Candidate readings matched to a timetable journey."),
    "obs_written": ("count", "Timing-point observations inserted or updated by the poll."),
    "dropped_insane": ("count", "Matched readings rejected by the accepted delay sanity range."),
    "stale": ("count", "Old repeated source positions rejected as stale."),
    "evidence_written": ("count", "Anomaly receipts admitted by the bounded evidence store."),
    "evidence_dropped": ("count", "Anomaly receipts refused by scope, quota, timestamp or error controls."),
    "evidence_deduplicated": ("count", "Repeated anomaly receipts intentionally represented by an existing bounded sample."),
    "evidence_scope_dropped": ("count", "Anomaly receipts excluded because the operator is outside the published audit."),
    "evidence_quota_dropped": ("count", "Anomaly receipts refused by a daily, time-band, operator or reserved-slot quota."),
    "evidence_errors": ("count", "Anomaly receipts not saved because their timestamp was invalid or diagnostics failed safely."),
    "siri_ref": ("identifier", "Timetable journey reference used to join SIRI-VM."),
    "direction": ("category", "Feed direction text or timetable direction number, depending on table."),
    "first_departure": ("HH:MM:SS", "First scheduled departure, allowing GTFS times after 24:00."),
    "last_departure": ("HH:MM:SS", "Last scheduled departure, allowing GTFS times after 24:00."),
    "route_id": ("identifier", "Timetable route identifier."),
    "service_id": ("identifier", "Timetable calendar/service identifier."),
    "block_id": ("identifier", "Timetable vehicle-duty/block identifier where supplied."),
    "vehicle_journey_code": ("identifier", "TransXChange vehicle-journey code where supplied."),
    "first_stop_id": ("identifier", "Timetable identity of the first stop."),
    "first_stop_code": ("identifier", "Public code of the first stop."),
    "timetable_edition": ("YYYYMMDD", "Resolved timetable edition start date where known."),
    "readings_in_gate": ("count", "Readings at most 150 metres from their timing point."),
    "readings_total": ("count", "All retained non-origin readings before the distance gate."),
    "excluded_distance": ("count", "Readings excluded for being outside the 150-metre gate."),
    "on_time_pct": ("percent", "On-time readings divided by in-gate readings."),
    "mean_delay_s": ("seconds", "Arithmetic mean delay for the group."),
    "median_delay_s": ("seconds", "Median delay for the group."),
    "early": ("count", "Readings more than 60 seconds early."),
    "late": ("count", "Readings at least 360 seconds late."),
    "median_gate_dist_m": ("metres", "Median GPS-to-stop distance for in-gate readings."),
    "expected_trips": ("count", "Scheduled trip denominator; known to be unstable in this period."),
    "observed_trips": ("count", "Distinct timetable trips with at least one retained observation."),
    "coverage_pct": ("percent", "Observed divided by expected trips; known denominator problems apply."),
    "scheduled_trips": ("count", "Scheduled trips in the group; expected-trip caveat applies."),
    "unobserved_trips": ("count", "Scheduled trips without a retained matching observation; not cancellations."),
    "exact_observed_trips": ("count", "Observed trips containing an exact-match reading."),
    "fuzzy_observed_trips": ("count", "Observed trips containing only fuzzy-match readings."),
    "unknown_tier_observed_trips": ("count", "Observed trips from before match tier was stored."),
    "candidate_readings": ("count", "Readings considered by the relevant calculation."),
    "matched_readings": ("count", "Candidate readings successfully matched."),
    "match_rate_pct": ("percent", "Matched candidate readings divided by candidates."),
    "n": ("count", "Rows in this histogram bucket."),
    "bucket": ("category", "Fixed delay bucket label."),
    "peak_band": ("category", "AM peak, interpeak, PM peak or evening band."),
    "geo_type": ("category", "Geographic grouping such as locality or ward."),
    "geo_key": ("identifier", "Name or code of the geographic grouping."),
    "source_operator": ("code", "Underlying operator for a pooled geography/route row."),
    "model": ("text", "Known vehicle model or honest unknown-model label."),
    "electric": ("boolean 0/1", "Whether the fleet source identifies the model as electric."),
    "fuel": ("category", "Fuel description from the fleet reference data."),
    "vehicles": ("count", "Distinct vehicle references contributing to the group."),
    "frequent": ("boolean 0/1", "Whether peak scheduled frequency reaches six buses per hour."),
    "peak_hourly": ("count per hour", "Largest scheduled departure count in an hour."),
    "time_band": ("category", "Hour or wider time band used by the rollup."),
    "is_valid": ("boolean 0/1", "Whether the rollup passed its stated completeness controls."),
    "poll_window_start": ("ISO datetime", "Start of the expected collection window."),
    "poll_window_end": ("ISO datetime", "End of the expected collection window."),
    "expected_polls": ("count", "Polls expected during the operating window."),
    "recorded_polls": ("count", "Poll rows retained during the operating window."),
    "successful_polls": ("count", "Successful polls during the operating window."),
    "successful_poll_rate_pct": ("percent", "Successful divided by recorded polls."),
    "successful_poll_coverage_pct": ("percent", "Successful polls divided by expected polls."),
    "max_successful_poll_gap_s": ("seconds", "Largest gap between successful polls."),
    "invalid_departure_times": ("count", "Scheduled trips whose first-departure time could not be parsed."),
    "evidence_id": ("identifier", "Bounded anomaly-receipt identifier."),
    "captured_at": ("ISO UTC datetime", "Time the anomaly receipt was saved."),
    "sampling_date": ("local YYYYMMDD date", "Local date used for bounded diagnostic sampling."),
    "sampling_band": ("category", "Four-hour local-time band used for bounded diagnostic sampling."),
    "sampling_reason": ("category", "Primary anomaly reason used for bounded diagnostic sampling."),
    "journey_ref": ("identifier", "Journey reference supplied by SIRI-VM for the receipt."),
    "origin_aimed_departure": ("text", "Origin departure value supplied by SIRI-VM."),
    "origin_ref": ("identifier", "Origin stop reference supplied by SIRI-VM."),
    "destination_ref": ("identifier", "Destination stop reference supplied by SIRI-VM."),
    "bearing": ("degrees", "Vehicle bearing supplied by SIRI-VM; coordinates are deliberately omitted."),
    "block_ref": ("identifier", "Vehicle-duty or block reference supplied by SIRI-VM."),
    "chosen_trip_id": ("identifier", "Timetable trip selected by the matcher."),
    "candidate_count": ("count", "Plausible timetable candidates considered when saved."),
    "candidates_truncated": ("boolean 0/1", "Whether diagnostic candidates exceeded the saved limit."),
    "delay_s": ("seconds", "Live estimate associated with the saved receipt."),
    "event_type": ("category", "Collector event type associated with the receipt."),
    "timetable_route_id": ("identifier", "Chosen timetable route identifier."),
    "timetable_service_id": ("identifier", "Chosen timetable service/calendar identifier."),
    "timetable_direction_id": ("category", "Chosen timetable direction number."),
    "event_id": ("identifier", "Derived rare-working event identifier."),
    "evaluated_at": ("ISO datetime", "Time the derived candidate was evaluated."),
    "profile_slug": ("identifier", "Vehicle profile identifier where one exists."),
    "vehicle_days": ("count", "Days this vehicle was observed in the baseline."),
    "route_days": ("count", "Days this route was observed in the baseline."),
    "pair_days": ("count", "Days this vehicle-and-route pair was observed."),
    "recent_pair_days": ("count", "Recent days this vehicle-and-route pair was observed."),
    "candidate_points": ("count", "Timing points supporting the derived candidate."),
    "queued": ("boolean 0/1", "Whether the derived candidate entered the private review queue."),
    "duty_detail_trips": ("count", "Scheduled trips with usable duty/block detail."),
    "duty_detail_pct": ("percent", "Scheduled trips with usable duty detail."),
    "scheduled_blocks": ("count", "Distinct scheduled duty blocks."),
    "usable_blocks": ("count", "Duty blocks passing the gap-analysis controls."),
    "missing_middle_trips": ("count", "Scheduled middle trips absent between observed duty trips."),
    "same_vehicle_gaps": ("count", "Gaps with the same vehicle observed before and after."),
    "short_connection_gaps": ("count", "Gaps whose surrounding connection times were plausible."),
    "ambiguous_match_gaps": ("count", "Gaps rejected because the surrounding match was ambiguous."),
    "strict_candidate_gaps": ("count", "Candidates passing every duty-gap control; not cancellations."),
    "previous_trip_id": ("identifier", "Observed trip before the candidate gap."),
    "previous_route": ("text", "Route before the candidate gap."),
    "previous_departure": ("HH:MM:SS", "Scheduled departure before the candidate gap."),
    "previous_siri_ref": ("identifier", "SIRI journey reference before the candidate gap."),
    "next_trip_id": ("identifier", "Observed trip after the candidate gap."),
    "next_route": ("text", "Route after the candidate gap."),
    "next_departure": ("HH:MM:SS", "Scheduled departure after the candidate gap."),
    "next_siri_ref": ("identifier", "SIRI journey reference after the candidate gap."),
    "connection_before_s": ("seconds", "Time between previous trip and candidate trip."),
    "connection_after_s": ("seconds", "Time between candidate trip and next trip."),
    "previous_match_window": ("boolean 0/1", "Whether the previous observation fell in the accepted match window."),
    "next_match_window": ("boolean 0/1", "Whether the next observation fell in the accepted match window."),
}


TABLE_OVERRIDES = {
    ("daily_fleet_summary", "on_time"): ("count", "On-time readings for this vehicle-model group."),
    ("daily_geo_summary", "on_time"): ("count", "On-time readings for this geographic group."),
    ("daily_geo_route_summary", "on_time"): ("count", "On-time readings for this geography-and-route group."),
    ("daily_overall_summary", "on_time"): ("count", "On-time readings for this daily operator group."),
    ("daily_peak_summary", "on_time"): ("count", "On-time readings for this route and time band."),
    ("daily_route_summary", "on_time"): ("count", "On-time readings for this daily route group."),
}


CAVEATS = (
    ("normalised_not_raw_siri", "high", None, None, "all",
     "This is normalised collector output. The original raw SIRI messages and discarded stale snapshots were deliberately never retained."),
    ("comparison_requires_investigation", "critical", None, None, "all",
     "No date cutoff certifies a safe comparison period. Validate the relevant routes, operators, timetable editions, measurement methods and samples before comparing results; later dates are not automatically reliable."),
    ("incomplete_start_day", "high", "20260602", "20260602", "timepoint_observations",
     "2 June is a partial starting day and must not be treated as a complete service day."),
    ("poll_history_starts_july", "high", None, "20260630", "poll_log",
     "Poll-level collection context is unavailable before 1 July."),
    ("damaged_july_first", "critical", "20260701", "20260701", "all",
     "1 July contains a damaged partial raw sample and is excluded from public results; do not use it as a complete day."),
    ("expected_trip_denominator_untrusted", "critical", None, None,
     "expected_trips,daily_trip_coverage,daily_overall_summary,daily_route_summary",
     "The scheduled-trip denominator changes implausibly across the window. Coverage and non-appearance values are research clues, not trustworthy findings."),
    ("early_running_anomaly_unresolved", "high", None, None, "timepoint_observations",
     "U1, U4, 13, 19 and 171 have unresolved early-running patterns. Trip matching, stop-visit assignment and timetable variants are hypotheses to test, not established causes for every route."),
    ("historical_stop_assignment_unresolved", "high", None, "20260815", "timepoint_observations",
     "Some retained trips place recorded stop visits in inconsistent time order before the 16 August collector change. Rebuilding summaries does not repair the underlying assignments; the inconsistent row is not necessarily the earlier stop."),
    ("collector_method_transition", "high", "20260816", "20260816", "punctuality",
     "Treat 16 August as a collector-method transition day, not a clean boundary within the day. Subsequent dates still require route-level validation."),
    ("origin_method_restatement", "high", None, "20260815", "punctuality",
     "On 16 August rollups were recalculated to exclude origin layovers, alongside a collector stop-selection change. This did not replay old raw SIRI through the new matcher or repair historical trip/stop assignments; figures across the change are not automatically comparable."),
    ("frequent_service_standard", "medium", None, None, "punctuality",
     "High-frequency services are normally assessed through excess waiting time, not the same timetable-punctuality percentage."),
    ("operator_coverage_imbalanced", "medium", None, None, "punctuality",
     "The pooled data is overwhelmingly First Bristol and does not measure every WECA operator or place evenly."),
    ("outlier_is_not_proof", "critical", None, None, "all",
     "A statistical or machine-learning outlier is a question to investigate, not proof of a cancellation, operator failure or software defect."),
)


REGIME_CHANGES = (
    ("20260602", "collector_history_starts_partial",
     "Retained timing-point history begins partway through the day.", "production row counts"),
    ("20260701", "poll_history_and_partial_cutover_day",
     "Poll history begins, but this day retained only a partial raw sample and is unsafe.", "published methodology"),
    ("20260713", "collector_replaced",
     "The matcher/collector rewrite changed distance rejection, candidate choice and stale-position handling.", "published methodology"),
    ("20260714", "transxchange_timing_points_restored",
     "Several TransXChange-sourced routes began producing timing-point readings after a flag conversion fix.", "published methodology"),
    ("20260722", "school_summer_holiday_context",
     "The school summer-holiday transition is an important traffic and timetable confound around this date.", "audit review context"),
    ("20260730", "abus_rows_begin",
     "Abus first appears in the retained pooled series, changing operator composition.", "published rollup observation"),
    ("20260816", "origin_exclusion_and_historical_restatement",
     "Origin layovers were excluded from rebuilt rollups and collector stop selection changed. Rebuilding used retained normalised observations, not a replay of old raw SIRI through the new matcher.", "published methodology and collector implementation"),
    ("20260823", "coverage_health_gate_added",
     "Trip coverage gained explicit poll-continuity and match-rate validity controls.", "published methodology"),
    ("20260831", "summer_bank_holiday",
     "Bank-holiday service and demand can differ from an ordinary Monday.", "calendar context"),
)


EXCLUDED_FIELDS = (
    ("matching_evidence", "lat", "Exact coordinates are not needed for this analysis; stop code and GPS distance provide safer context."),
    ("matching_evidence", "lon", "Exact coordinates are not needed for this analysis; stop code and GPS distance provide safer context."),
    ("matching_evidence", "reasons_json", "Parsed into matching_evidence_reasons through a fixed string allow-list."),
    ("matching_evidence", "calculation_reasons_json", "Parsed into matching_evidence_reasons through a fixed string allow-list."),
    ("matching_evidence", "alternatives_json", "Parsed into matching_evidence_alternatives through fixed known keys."),
    ("daily_fleet_summary", "routes_json", "Parsed into daily_fleet_routes."),
    ("daily_trip_coverage_days", "invalid_reasons_json", "Parsed into daily_trip_coverage_invalid_reasons."),
    ("daily_duty_gap_days", "invalid_reasons_json", "Parsed into daily_duty_gap_invalid_reasons."),
    ("rare_working_evidence", "evidence_json", "Rule-produced nested evidence is omitted; fixed scalar fields are retained."),
)


ALTERNATIVE_KEYS = (
    Column("trip_id", "TEXT"), Column("route_id", "TEXT"),
    Column("service_id", "TEXT"), Column("direction_id", "INTEGER"),
    Column("block_id", "TEXT"), Column("vehicle_journey_code", "TEXT"),
    Column("route", "TEXT"), Column("origin_departure", "TEXT"),
    Column("calendar_start", "TEXT"), Column("calendar_end", "TEXT"),
    Column("gps_distance_m", "INTEGER"), Column("timetable_edition", "TEXT"),
)


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def compact_date(value: str, argument: str) -> str:
    compact = value.strip().replace("-", "")
    try:
        parsed = datetime.strptime(compact, "%Y%m%d").date()
    except ValueError as exc:
        raise ResearchExportError(f"{argument} must be YYYYMMDD or YYYY-MM-DD") from exc
    return parsed.strftime("%Y%m%d")


def hyphenated(value: str) -> str:
    return datetime.strptime(value, "%Y%m%d").strftime("%Y-%m-%d")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_regular(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ResearchExportError(f"{label} does not exist: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ResearchExportError(f"{label} is not a regular file: {path}")
    return path.resolve(strict=True)


def ensure_export_root(path: Path) -> Path:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ResearchExportError(f"private export directory does not exist: {path}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise ResearchExportError("private export path is not a real directory")
    resolved = path.resolve(strict=True)
    if resolved == Path("/"):
        raise ResearchExportError("refusing filesystem root as export directory")
    return resolved


def remove_work_directory(work: Path, root: Path) -> None:
    """Remove only the exact private temporary directory created by this run."""
    if work.parent.resolve(strict=True) != root.resolve(strict=True) \
            or not work.name.startswith(".research-") or work.is_symlink():
        raise ResearchExportError("refusing unsafe temporary-directory cleanup")
    for attempt in range(5):
        try:
            shutil.rmtree(work)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 4:
                raise ResearchExportError(
                    f"could not remove exact private temporary directory {work.name}")
            time.sleep(0.1)


def open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=60000")
    connection.execute("PRAGMA query_only=ON")
    return connection


@contextmanager
def heavy_io_lock(path: Path, wait_seconds: int = LOCK_WAIT_SECONDS):
    if fcntl is None:
        # Production is Linux and always uses the shared lock. Windows reaches
        # this path only in isolated tests against temporary databases.
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ResearchExportError(
                        "another backup, rollup or data promotion still holds the heavy-I/O lock")
                time.sleep(1)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def table_columns(connection: sqlite3.Connection, name: str) -> set[str]:
    quoted = quote_identifier(name)
    return {row[1] for row in connection.execute(f"PRAGMA table_info({quoted})")}


def table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def verify_source_schema(connection: sqlite3.Connection) -> list[TableSpec]:
    available = []
    for spec in TABLE_SPECS:
        if not table_exists(connection, spec.name):
            if spec.required:
                raise ResearchExportError(f"required source table is missing: {spec.name}")
            continue
        missing = {column.name for column in spec.columns} - table_columns(connection, spec.name)
        if missing:
            raise ResearchExportError(
                f"{spec.name} is missing allow-listed columns: {', '.join(sorted(missing))}")
        available.append(spec)
    return available


def available_period(connection: sqlite3.Connection) -> tuple[str, str]:
    row = connection.execute(
        "SELECT MIN(service_date), MAX(service_date) FROM timepoint_observations"
    ).fetchone()
    if not row or not row[0] or not row[1]:
        raise ResearchExportError("timepoint_observations contains no dated rows")
    latest_closed = (datetime.now(LONDON).date() - timedelta(days=1)).strftime("%Y%m%d")
    last = connection.execute(
        "SELECT MAX(service_date) FROM timepoint_observations WHERE service_date<=?",
        (latest_closed,),
    ).fetchone()[0]
    if not last:
        raise ResearchExportError("no closed service day is retained")
    return str(row[0]), str(last)


def resolve_period(connection: sqlite3.Connection, from_value: str | None,
                   to_value: str | None) -> tuple[str, str]:
    available_from, available_to = available_period(connection)
    date_from = compact_date(from_value, "--from") if from_value else available_from
    date_to = compact_date(to_value, "--to") if to_value else available_to
    if date_from > date_to:
        raise ResearchExportError("--from must not be later than --to")
    if date_from < available_from or date_to > available_to:
        raise ResearchExportError(
            f"requested closed period must be inside {available_from} to {available_to}")
    return date_from, date_to


def backup_snapshot(source_path: Path, snapshot: Path) -> dict:
    started = time.monotonic()
    with closing(open_read_only(source_path)) as source:
        source_schema = source.execute("PRAGMA schema_version").fetchone()[0]
        source_pages = source.execute("PRAGMA page_count").fetchone()[0]
        source_page_size = source.execute("PRAGMA page_size").fetchone()[0]
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination, pages=4096, sleep=0.05)
            destination.execute("PRAGMA journal_mode=DELETE")
        finally:
            destination.close()
        if source.total_changes != 0:
            raise ResearchExportError("read-only source connection unexpectedly reports changes")
    os.chmod(snapshot, 0o600)
    with snapshot.open("r+b") as handle:
        os.fsync(handle.fileno())
    return {
        "source_schema_version": source_schema,
        "source_page_count": source_pages,
        "source_page_size": source_page_size,
        "source_read_only": True,
        "source_connection_total_changes": 0,
        "snapshot_bytes": snapshot.stat().st_size,
        "snapshot_sha256": sha256_file(snapshot),
        "snapshot_seconds": round(time.monotonic() - started, 2),
    }


def create_table(connection: sqlite3.Connection, name: str,
                 selected_columns: Iterable[Column]) -> None:
    definition = ", ".join(
        f"{quote_identifier(column.name)} {column.declared_type}"
        for column in selected_columns
    )
    connection.execute(f"CREATE TABLE {quote_identifier(name)} ({definition})")


def source_bounds(spec: TableSpec, date_from: str, date_to: str) -> tuple[str, str]:
    if spec.hyphenated_date:
        return hyphenated(date_from), hyphenated(date_to)
    return date_from, date_to


def copy_table(source: sqlite3.Connection, destination: sqlite3.Connection,
               spec: TableSpec, date_from: str, date_to: str) -> dict:
    create_table(destination, spec.name, spec.columns)
    names = [column.name for column in spec.columns]
    selected = ", ".join(quote_identifier(name) for name in names)
    ordered = ", ".join(quote_identifier(name) for name in spec.order_by)
    start, end = source_bounds(spec, date_from, date_to)
    query = (
        f"SELECT {selected} FROM {quote_identifier(spec.name)} "
        f"WHERE {spec.date_expression} BETWEEN ? AND ? ORDER BY {ordered}"
    )
    placeholders = ",".join("?" for _ in names)
    insert = (
        f"INSERT INTO {quote_identifier(spec.name)} ({selected}) "
        f"VALUES ({placeholders})"
    )
    cursor = source.execute(query, (start, end))
    count = 0
    while rows := cursor.fetchmany(5000):
        destination.executemany(insert, (tuple(row) for row in rows))
        count += len(rows)
    first_last = destination.execute(
        f"SELECT MIN({spec.date_expression}), MAX({spec.date_expression}) "
        f"FROM {quote_identifier(spec.name)}"
    ).fetchone()
    return {"rows": count, "first_date": first_last[0], "last_date": first_last[1]}


def parse_string_list(value: object, *, table: str, row_id: str,
                      field: str, limit: int = 128) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError) as exc:
        raise ResearchExportError(f"{table} {row_id} has invalid {field}") from exc
    if not isinstance(parsed, list) or any(
            not isinstance(item, str) or not item or len(item) > limit for item in parsed):
        raise ResearchExportError(f"{table} {row_id} has unsafe {field}")
    return parsed


def parse_dict_list(value: object, *, row_id: str) -> list[dict]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError) as exc:
        raise ResearchExportError(
            f"matching_evidence {row_id} has invalid alternatives_json") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, dict) for item in parsed):
        raise ResearchExportError(
            f"matching_evidence {row_id} has unsafe alternatives_json")
    if len(parsed) > 3:
        raise ResearchExportError(
            f"matching_evidence {row_id} exceeds the saved alternatives limit")
    return parsed


def copy_matching_children(source: sqlite3.Connection,
                           destination: sqlite3.Connection,
                           date_from: str, date_to: str) -> dict[str, int]:
    reason_columns = columns("evidence_id:TEXT reason_kind:TEXT reason:TEXT")
    create_table(destination, "matching_evidence_reasons", reason_columns)
    alt_columns = (Column("evidence_id", "TEXT"), Column("alternative_rank", "INTEGER"),
                   *ALTERNATIVE_KEYS)
    create_table(destination, "matching_evidence_alternatives", alt_columns)
    reason_rows = []
    alternative_rows = []
    query = (
        "SELECT evidence_id,reasons_json,calculation_reasons_json,alternatives_json "
        "FROM matching_evidence WHERE service_date BETWEEN ? AND ? "
        "ORDER BY captured_at,evidence_id"
    )
    for row in source.execute(query, (date_from, date_to)):
        evidence_id = str(row[0])
        for field, kind in (("reasons_json", "selection"),
                            ("calculation_reasons_json", "calculation")):
            value = row[1] if field == "reasons_json" else row[2]
            for reason in parse_string_list(
                    value, table="matching_evidence", row_id=evidence_id, field=field):
                reason_rows.append((evidence_id, kind, reason))
        for rank, item in enumerate(parse_dict_list(row[3], row_id=evidence_id), 1):
            values = []
            for column in ALTERNATIVE_KEYS:
                value = item.get(column.name)
                if isinstance(value, (dict, list)):
                    raise ResearchExportError(
                        f"matching_evidence {evidence_id} has nested alternative field {column.name}")
                if isinstance(value, str) and len(value) > 256:
                    raise ResearchExportError(
                        f"matching_evidence {evidence_id} has oversized alternative field {column.name}")
                values.append(value)
            alternative_rows.append((evidence_id, rank, *values))
    destination.executemany(
        "INSERT INTO matching_evidence_reasons VALUES (?,?,?)", reason_rows)
    placeholders = ",".join("?" for _ in alt_columns)
    destination.executemany(
        f"INSERT INTO matching_evidence_alternatives VALUES ({placeholders})",
        alternative_rows)
    return {
        "matching_evidence_reasons": len(reason_rows),
        "matching_evidence_alternatives": len(alternative_rows),
    }


def copy_json_string_children(source: sqlite3.Connection,
                              destination: sqlite3.Connection,
                              *, source_table: str, source_id_columns: tuple[str, ...],
                              json_column: str, destination_table: str,
                              child_column: str, date_from: str,
                              date_to: str) -> int:
    child_columns = tuple(Column(name, "TEXT") for name in source_id_columns) + (
        Column(child_column, "TEXT"),)
    create_table(destination, destination_table, child_columns)
    selected = ",".join(quote_identifier(name) for name in (*source_id_columns, json_column))
    query = (
        f"SELECT {selected} FROM {quote_identifier(source_table)} "
        "WHERE service_date BETWEEN ? AND ? ORDER BY service_date"
    )
    rows = []
    for row in source.execute(query, (date_from, date_to)):
        row_id = "/".join(str(value) for value in row[:-1])
        for value in parse_string_list(
                row[-1], table=source_table, row_id=row_id, field=json_column, limit=256):
            rows.append((*row[:-1], value))
    placeholders = ",".join("?" for _ in child_columns)
    destination.executemany(
        f"INSERT INTO {quote_identifier(destination_table)} VALUES ({placeholders})", rows)
    return len(rows)


def copy_fleet_routes(source: sqlite3.Connection,
                      destination: sqlite3.Connection,
                      date_from: str, date_to: str) -> int:
    """Normalise the rollup's JSON ``[[route, reading_count], ...]`` pairs."""
    child_columns = columns(
        "service_date:TEXT operator:TEXT model:TEXT route:TEXT readings:INTEGER")
    create_table(destination, "daily_fleet_routes", child_columns)
    rows = []
    for row in source.execute(
            "SELECT service_date,operator,model,routes_json "
            "FROM daily_fleet_summary WHERE service_date BETWEEN ? AND ? "
            "ORDER BY service_date,operator,model", (date_from, date_to)):
        row_id = "/".join(str(value) for value in row[:-1])
        try:
            parsed = json.loads(str(row[-1] or "[]"))
        except (TypeError, ValueError) as exc:
            raise ResearchExportError(
                f"daily_fleet_summary {row_id} has invalid routes_json") from exc
        if not isinstance(parsed, list) or len(parsed) > 8:
            raise ResearchExportError(
                f"daily_fleet_summary {row_id} has unsafe routes_json")
        for item in parsed:
            if not isinstance(item, list) or len(item) != 2:
                raise ResearchExportError(
                    f"daily_fleet_summary {row_id} has unsafe routes_json")
            route, readings = item
            if not isinstance(route, str) or not route or len(route) > 256 \
                    or isinstance(readings, bool) or not isinstance(readings, int) \
                    or readings < 1:
                raise ResearchExportError(
                    f"daily_fleet_summary {row_id} has unsafe routes_json")
            rows.append((*row[:-1], route, readings))
    destination.executemany(
        "INSERT INTO daily_fleet_routes VALUES (?,?,?,?,?)", rows)
    return len(rows)


def dictionary_entry(spec: TableSpec, column: Column) -> tuple:
    unit, description = TABLE_OVERRIDES.get(
        (spec.name, column.name), COMMON_DOCS.get(column.name, (None, None)))
    if not description:
        raise ResearchExportError(
            f"data dictionary is missing {spec.name}.{column.name}")
    return (spec.name, column.name, column.declared_type, unit or "", description, "copied")


def create_metadata(destination: sqlite3.Connection, *, date_from: str,
                    date_to: str, generated_at: str, source_fingerprint: dict,
                    table_results: dict[str, dict], derived_counts: dict[str, int],
                    available_specs: list[TableSpec]) -> None:
    destination.executescript(
        """
        CREATE TABLE research_manifest (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE research_table_manifest (
            table_name TEXT PRIMARY KEY, role TEXT NOT NULL, required INTEGER NOT NULL,
            source_rows INTEGER NOT NULL, exported_rows INTEGER NOT NULL,
            first_date TEXT, last_date TEXT, selection TEXT NOT NULL,
            description TEXT NOT NULL
        );
        CREATE TABLE research_data_dictionary (
            table_name TEXT NOT NULL, column_name TEXT NOT NULL,
            declared_type TEXT NOT NULL, unit TEXT NOT NULL,
            description TEXT NOT NULL, source_status TEXT NOT NULL,
            PRIMARY KEY (table_name, column_name)
        );
        CREATE TABLE research_caveats (
            code TEXT PRIMARY KEY, severity TEXT NOT NULL, date_from TEXT,
            date_to TEXT, applies_to TEXT NOT NULL, plain_english TEXT NOT NULL
        );
        CREATE TABLE research_regime_changes (
            effective_date TEXT NOT NULL, code TEXT PRIMARY KEY,
            plain_english TEXT NOT NULL, source TEXT NOT NULL
        );
        CREATE TABLE research_excluded_fields (
            table_name TEXT NOT NULL, column_name TEXT NOT NULL,
            reason TEXT NOT NULL, PRIMARY KEY (table_name, column_name)
        );
        CREATE TABLE research_day_counts (
            service_date TEXT PRIMARY KEY, observation_rows INTEGER NOT NULL,
            poll_rows INTEGER NOT NULL, expected_trip_rows INTEGER NOT NULL,
            matching_receipt_rows INTEGER NOT NULL,
            recommended_general_comparison INTEGER NOT NULL,
            warning_codes TEXT NOT NULL
        );
        """
    )
    manifest = {
        "schema_version": str(SCHEMA_VERSION),
        "generated_at": generated_at,
        "date_from": date_from,
        "date_to": date_to,
        "timezone": "Europe/London",
        "selection": "complete_census_no_sampling",
        "raw_siri_retained": "false",
        "source_database": str(DEFAULT_AUDIT_DB),
        "source_read_only": "true",
        "source_connection_total_changes": "0",
        "source_snapshot_sha256": source_fingerprint["snapshot_sha256"],
        "source_snapshot_bytes": str(source_fingerprint["snapshot_bytes"]),
        "comparison_guidance_version": "2",
        "comparison_status": "requires_investigation",
        # Retain the v1 key, but do not silently recommend every date after an
        # empty string. Readers must treat the explicit sentinel as no endorsement.
        "recommended_general_comparison_from": "none",
        "expected_trip_denominator_trusted": "false",
        "july_first_complete": "false",
    }
    destination.executemany(
        "INSERT INTO research_manifest VALUES (?,?)", sorted(manifest.items()))
    spec_by_name = {spec.name: spec for spec in available_specs}
    table_rows = []
    dictionary_rows = []
    for name, result in table_results.items():
        spec = spec_by_name[name]
        table_rows.append((
            name, spec.role, int(spec.required), result["rows"], result["rows"],
            result["first_date"], result["last_date"], "census", spec.description,
        ))
        dictionary_rows.extend(dictionary_entry(spec, column) for column in spec.columns)
    derived_descriptions = {
        "matching_evidence_reasons": "Fixed parsed reason strings from rule-selected receipts.",
        "matching_evidence_alternatives": "Fixed known fields for alternative timetable candidates.",
        "daily_fleet_routes": "Route labels and reading counts parsed from the fleet rollup's bounded route list.",
        "daily_trip_coverage_invalid_reasons": "Fixed validity reasons parsed from daily trip-coverage health.",
        "daily_duty_gap_invalid_reasons": "Fixed validity reasons parsed from daily duty-gap health.",
    }
    derived_columns = {
        "matching_evidence_reasons": columns("evidence_id:TEXT reason_kind:TEXT reason:TEXT"),
        "matching_evidence_alternatives": (
            Column("evidence_id", "TEXT"), Column("alternative_rank", "INTEGER"),
            *ALTERNATIVE_KEYS),
        "daily_fleet_routes": columns(
            "service_date:TEXT operator:TEXT model:TEXT route:TEXT readings:INTEGER"),
        "daily_trip_coverage_invalid_reasons": columns("service_date:TEXT reason:TEXT"),
        "daily_duty_gap_invalid_reasons": columns("service_date:TEXT operator:TEXT reason:TEXT"),
    }
    derived_docs = {
        "reason_kind": ("category", "Whether the reason selected the receipt or came from delay calculation."),
        "reason": ("code", "Bounded internal reason code, not a conclusion."),
        "alternative_rank": ("ordinal", "Saved order of an alternative timetable candidate."),
        "direction_id": ("category", "Alternative timetable direction number."),
        "origin_departure": ("HH:MM:SS", "Alternative candidate origin departure."),
        "calendar_start": ("YYYYMMDD", "Alternative candidate calendar start."),
        "calendar_end": ("YYYYMMDD", "Alternative candidate calendar end."),
        "readings": ("count", "Readings for this vehicle model on this route."),
    }
    for name, count in derived_counts.items():
        table_rows.append((name, "normalised_from_bounded_json", 0, count, count,
                           date_from, date_to, "all parsed children",
                           derived_descriptions[name]))
        for column in derived_columns[name]:
            unit, description = derived_docs.get(
                column.name, COMMON_DOCS.get(column.name, ("", None)))
            if not description:
                raise ResearchExportError(
                    f"data dictionary is missing {name}.{column.name}")
            dictionary_rows.append((name, column.name, column.declared_type,
                                    unit or "", description, "parsed"))
    destination.executemany(
        "INSERT INTO research_table_manifest VALUES (?,?,?,?,?,?,?,?,?)", table_rows)
    destination.executemany(
        "INSERT INTO research_data_dictionary VALUES (?,?,?,?,?,?)", dictionary_rows)
    destination.executemany(
        "INSERT INTO research_caveats VALUES (?,?,?,?,?,?)", CAVEATS)
    destination.executemany(
        "INSERT INTO research_regime_changes VALUES (?,?,?,?)", REGIME_CHANGES)
    destination.executemany(
        "INSERT INTO research_excluded_fields VALUES (?,?,?)", EXCLUDED_FIELDS)

    counts = {}
    for name, date_sql in (
        ("timepoint_observations", "service_date"),
        ("expected_trips", "service_date"),
        ("matching_evidence", "service_date"),
    ):
        counts[name] = dict(destination.execute(
            f"SELECT {date_sql},COUNT(*) FROM {quote_identifier(name)} "
            f"GROUP BY {date_sql}"
        ))
    counts["poll_log"] = {
        value.replace("-", ""): count for value, count in destination.execute(
            "SELECT substr(poll_at,1,10),COUNT(*) FROM poll_log GROUP BY substr(poll_at,1,10)")
    }
    first = datetime.strptime(date_from, "%Y%m%d").date()
    last = datetime.strptime(date_to, "%Y%m%d").date()
    day_rows = []
    current = first
    while current <= last:
        value = current.strftime("%Y%m%d")
        warnings = ["comparison_requires_investigation",
                    "expected_trip_denominator_untrusted"]
        if value == "20260602":
            warnings.append("incomplete_start_day")
        if value < "20260701":
            warnings.append("poll_history_unavailable")
        if value == "20260701":
            warnings.extend(("damaged_july_first", "partial_poll_day"))
        if value == "20260831":
            warnings.append("summer_bank_holiday")
        if value < "20260816":
            warnings.append("historical_stop_assignment_unresolved")
        if value == "20260816":
            warnings.append("collector_method_transition")
        day_rows.append((
            value,
            int(counts["timepoint_observations"].get(value, 0)),
            int(counts["poll_log"].get(value, 0)),
            int(counts["expected_trips"].get(value, 0)),
            int(counts["matching_evidence"].get(value, 0)),
            0,  # No whole day is certified for general comparisons by this export.
            ",".join(sorted(set(warnings))),
        ))
        current += timedelta(days=1)
    destination.executemany(
        "INSERT INTO research_day_counts VALUES (?,?,?,?,?,?,?)", day_rows)


def readme_text(date_from: str, date_to: str,
                table_results: dict[str, dict]) -> str:
    counts = "\n".join(
        f"  - {name}: {result['rows']:,} rows"
        for name, result in table_results.items()
    )
    return f"""Bristol Bus Bot private collector research dataset
===================================================

Period: {date_from} to {date_to}, inclusive service dates (Europe/London)
Selection: complete census; no rows were sampled
Format: SQLite 3 database, {DATABASE_MEMBER}

What this is
------------
This is a private research copy of normalised collector output, poll health,
scheduled-trip snapshots, bounded rule-selected evidence and permanent daily
rollups. It is intended for exploratory statistics and machine-assisted
pattern finding. It is not official operator data.

Important: read these before analysis
--------------------------------------
* The original raw SIRI messages were deliberately never retained.
* 2 June is an incomplete starting day.
* Poll-level history does not exist before 1 July.
* 1 July is a damaged partial day and must not be treated as complete.
* expected_trips is unstable across this period. Coverage and non-appearance
  figures are clues for investigation, not trustworthy findings.
* U1, U4, 13, 19 and 171 have unresolved implausible early-running patterns
  with trip matching, stop-visit assignment and timetable variants among the
  hypotheses to test; a single cause has not been established for all routes.
* The collector changed on 13 July; TransXChange timing points were restored
  on 14 July. On 16 August rollups were rebuilt to exclude origin layovers,
  alongside a collector stop-selection change. That rebuild did not replay
  old raw SIRI through the new matcher or repair historical assignments.
* Some pre-16-August trips have inconsistent recorded stop order. Treat
  16 August as a transition day, not a within-day cutoff. Later observations
  still need validation; do not just move a "safe from" date to 17 August.
* School holidays, the bank holiday and operator additions are also confounders.
* A machine-generated outlier is a question, not proof of a cancellation,
  operator failure or software bug.

Comparison guidance (version 2)
-------------------------------
No date cutoff certifies a safe comparison period. Check the relevant routes,
operators, timetable editions, measurement methods and sample sizes before
making before/after or cross-route claims. Exploration of the full export is
still useful: a warning is not proof that every reading is wrong.

For compatibility, research_manifest retains recommended_general_comparison_from
with the literal value "none" (not a date). comparison_status is
"requires_investigation" and comparison_guidance_version is "2".
research_day_counts.recommended_general_comparison is 0 for every day: no blanket
endorsement, not an instruction to discard the evidence. The warning codes and
research_caveats explain why. The table layout remains export schema version 1.

The database repeats these points in research_caveats and records dated changes
in research_regime_changes. research_day_counts flags unsafe or incomplete
days. research_data_dictionary explains every copied or normalised data field.
The small `research_*` tables describe the export itself. No exact receipt
latitude/longitude, credentials, environment values, social data or arbitrary
nested JSON is included.

Source tables
-------------
{counts}

Quick start in Python
---------------------
    import sqlite3
    import pandas as pd

    db = sqlite3.connect("file:{DATABASE_MEMBER}?mode=ro", uri=True)
    manifest = pd.read_sql_query("SELECT * FROM research_manifest", db)
    caveats = pd.read_sql_query("SELECT * FROM research_caveats", db)
    days = pd.read_sql_query("SELECT * FROM research_day_counts", db)
    # Exploratory rows only: not a validated comparison cohort.
    observations = pd.read_sql_query('''
        SELECT * FROM timepoint_observations
        WHERE service_date BETWEEN '{date_from}' AND '{date_to}'
    ''', db)

Useful first commands
---------------------
    SELECT * FROM research_manifest ORDER BY key;
    SELECT * FROM research_caveats ORDER BY severity, code;
    SELECT * FROM research_regime_changes ORDER BY effective_date;
    SELECT * FROM research_day_counts ORDER BY service_date;
    SELECT table_name, exported_rows FROM research_table_manifest;

Real route, stop, trip, journey and vehicle references are retained because
they come from the public feed and are needed for joins and follow-up evidence
packs. A vehicle reference over time can resemble a duty, but no driver name or
person identifier is present.
"""


def build_database(snapshot: Path, database: Path, *, date_from: str,
                   date_to: str, generated_at: str,
                   source_fingerprint: dict) -> tuple[dict[str, dict], dict[str, int]]:
    with closing(open_read_only(snapshot)) as source:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ResearchExportError("private source snapshot failed SQLite quick_check")
        available_specs = verify_source_schema(source)
        destination = sqlite3.connect(database)
        try:
            destination.execute("PRAGMA journal_mode=OFF")
            destination.execute("PRAGMA synchronous=OFF")
            destination.execute("PRAGMA temp_store=MEMORY")
            destination.execute("BEGIN")
            table_results = {
                spec.name: copy_table(source, destination, spec, date_from, date_to)
                for spec in available_specs
            }
            derived_counts = copy_matching_children(
                source, destination, date_from, date_to)
            if table_exists(source, "daily_fleet_summary"):
                derived_counts["daily_fleet_routes"] = copy_fleet_routes(
                    source, destination, date_from, date_to)
            if table_exists(source, "daily_trip_coverage_days"):
                derived_counts["daily_trip_coverage_invalid_reasons"] = copy_json_string_children(
                    source, destination,
                    source_table="daily_trip_coverage_days",
                    source_id_columns=("service_date",),
                    json_column="invalid_reasons_json",
                    destination_table="daily_trip_coverage_invalid_reasons",
                    child_column="reason", date_from=date_from, date_to=date_to)
            if table_exists(source, "daily_duty_gap_days"):
                derived_counts["daily_duty_gap_invalid_reasons"] = copy_json_string_children(
                    source, destination,
                    source_table="daily_duty_gap_days",
                    source_id_columns=("service_date", "operator"),
                    json_column="invalid_reasons_json",
                    destination_table="daily_duty_gap_invalid_reasons",
                    child_column="reason", date_from=date_from, date_to=date_to)
            create_metadata(
                destination, date_from=date_from, date_to=date_to,
                generated_at=generated_at, source_fingerprint=source_fingerprint,
                table_results=table_results, derived_counts=derived_counts,
                available_specs=available_specs)
            destination.commit()
            destination.execute("PRAGMA journal_mode=DELETE")
        except Exception:
            destination.rollback()
            raise
        finally:
            destination.close()
    os.chmod(database, 0o600)
    if database.stat().st_size > MAX_DATABASE_BYTES:
        raise ResearchExportError(
            f"research database exceeds {MAX_DATABASE_BYTES} bytes")
    with closing(sqlite3.connect(
            database.as_uri() + "?mode=ro", uri=True)) as check:
        check.execute("PRAGMA query_only=ON")
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ResearchExportError("research database failed SQLite quick_check")
        for name, result in table_results.items():
            actual = check.execute(
                f"SELECT COUNT(*) FROM {quote_identifier(name)}").fetchone()[0]
            if actual != result["rows"]:
                raise ResearchExportError(f"row-count verification failed for {name}")
    return table_results, derived_counts


def build_archive(database: Path, readme: Path, archive: Path, *,
                  date_from: str, date_to: str, table_results: dict[str, dict],
                  generated_at: str) -> dict:
    database_sha = sha256_file(database)
    readme_sha = sha256_file(readme)
    comment = {
        "schema_version": SCHEMA_VERSION,
        "date_from": date_from,
        "date_to": date_to,
        "generated_at": generated_at,
        "database_member": DATABASE_MEMBER,
        "database_bytes": database.stat().st_size,
        "database_sha256": database_sha,
        "readme_member": README_MEMBER,
        "readme_sha256": readme_sha,
        "selection": "complete_census_no_sampling",
        "row_counts": {name: result["rows"] for name, result in table_results.items()},
    }
    with zipfile.ZipFile(
            archive, "x", compression=zipfile.ZIP_DEFLATED,
            compresslevel=6, allowZip64=True) as output:
        output.write(database, DATABASE_MEMBER)
        output.write(readme, README_MEMBER)
        output.comment = json.dumps(comment, sort_keys=True, separators=(",", ":")).encode()
    os.chmod(archive, 0o600)
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ResearchExportError(f"research archive exceeds {MAX_ARCHIVE_BYTES} bytes")
    with zipfile.ZipFile(archive) as check:
        if set(check.namelist()) != {DATABASE_MEMBER, README_MEMBER}:
            raise ResearchExportError("research archive contains unexpected members")
        if check.testzip() is not None:
            raise ResearchExportError("research archive failed CRC verification")
    return {
        "archive_bytes": archive.stat().st_size,
        "archive_sha256": sha256_file(archive),
        "database_bytes": database.stat().st_size,
        "database_sha256": database_sha,
        "readme_sha256": readme_sha,
    }


def create_export(*, audit_db: Path = DEFAULT_AUDIT_DB,
                  export_root: Path = DEFAULT_EXPORT_ROOT,
                  lock_path: Path = DEFAULT_LOCK, request_id: str,
                  from_value: str | None = None,
                  to_value: str | None = None,
                  now: datetime | None = None) -> dict:
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise ResearchExportError("--request-id must be exactly 12 lowercase hex characters")
    source_path = ensure_regular(audit_db, "audit database")
    root = ensure_export_root(export_root)
    if shutil.disk_usage(root).free < MIN_FREE_BYTES:
        raise ResearchExportError(
            f"private export filesystem needs at least {MIN_FREE_BYTES} free bytes")
    generated = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = generated.isoformat()
    started = time.monotonic()
    work = Path(tempfile.mkdtemp(prefix=f".research-{request_id}-", dir=root))
    os.chmod(work, 0o700)
    snapshot = work / "source-snapshot.sqlite"
    database = work / DATABASE_MEMBER
    readme = work / README_MEMBER
    candidate = work / "archive.zip"
    published: Path | None = None
    try:
        if hasattr(os, "nice"):
            try:
                os.nice(10)
            except OSError:
                pass
        with heavy_io_lock(lock_path):
            fingerprint = backup_snapshot(source_path, snapshot)
            with closing(open_read_only(snapshot)) as frozen:
                date_from, date_to = resolve_period(frozen, from_value, to_value)
            name = f"collector-research-{date_from}-to-{date_to}-{request_id}.zip"
            if not ARCHIVE_NAME_RE.fullmatch(name):
                raise ResearchExportError("generated archive name is unsafe")
            published = root / name
            if published.exists() or published.is_symlink():
                raise ResearchExportError("request output already exists")
            table_results, derived_counts = build_database(
                snapshot, database, date_from=date_from, date_to=date_to,
                generated_at=generated_at, source_fingerprint=fingerprint)
            readme.write_text(
                readme_text(date_from, date_to, table_results), encoding="utf-8")
            os.chmod(readme, 0o600)
            archive_result = build_archive(
                database, readme, candidate, date_from=date_from, date_to=date_to,
                table_results=table_results, generated_at=generated_at)
            os.link(candidate, published)
            os.chmod(published, 0o600)
        result = {
            "status": "created",
            "schema_version": SCHEMA_VERSION,
            "remote_filename": published.name,
            "date_from": date_from,
            "date_to": date_to,
            "selection": "complete_census_no_sampling",
            "row_counts": {name: value["rows"] for name, value in table_results.items()},
            "derived_row_counts": derived_counts,
            "source_read_only": True,
            "source_connection_total_changes": 0,
            "snapshot_seconds": fingerprint["snapshot_seconds"],
            "elapsed_seconds": round(time.monotonic() - started, 2),
            **archive_result,
        }
        return result
    except Exception:
        if published is not None:
            try:
                published.unlink()
            except FileNotFoundError:
                pass
        raise
    finally:
        remove_work_directory(work, root)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request-id", required=True,
                        help="12 lowercase hex characters generated by the downloader")
    parser.add_argument("--from", dest="from_value",
                        help="first service date; defaults to earliest retained row")
    parser.add_argument("--to", dest="to_value",
                        help="last closed service date; defaults to latest retained closed day")
    parser.add_argument("--audit-db", type=Path, default=DEFAULT_AUDIT_DB,
                        help=argparse.SUPPRESS)
    parser.add_argument("--export-root", type=Path, default=DEFAULT_EXPORT_ROOT,
                        help=argparse.SUPPRESS)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK,
                        help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = create_export(
            audit_db=args.audit_db, export_root=args.export_root,
            lock_path=args.lock, request_id=args.request_id,
            from_value=args.from_value, to_value=args.to_value)
    except (ResearchExportError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Environment-driven collector configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class Config:
    bods_api_key: str = field(default_factory=lambda: os.getenv("BODS_API_KEY", ""))
    bounding_box: str = field(default_factory=lambda: os.getenv(
        "BBB_BOUNDING_BOX",
        "-3.1150604039022,51.2730967430816,-2.25213125341167,51.6773024336158"))
    timetable_db: str = field(default_factory=lambda: os.getenv(
        "BBB_TIMETABLE_DB", "timetable.db"))
    live_db: str = field(default_factory=lambda: os.getenv("BBB_LIVE_DB", "live.db"))
    audit_db: str = field(default_factory=lambda: os.getenv("BBB_AUDIT_DB", "audit.db"))
    boundary_geojson: str = field(default_factory=lambda: os.getenv(
        "BBB_BOUNDARY", "weca_boundary_dissolved.geojson"))
    target_tz: str = field(default_factory=lambda: os.getenv(
        "BBB_TZ", "Europe/London"))

    poll_interval_s: float = field(default_factory=lambda: _f("BBB_POLL_INTERVAL_S", 30))
    sx_poll_interval_s: float = field(default_factory=lambda: _f("BBB_SX_POLL_INTERVAL_S", 300))
    fetch_timeout_s: float = field(default_factory=lambda: _f("BBB_FETCH_TIMEOUT_S", 30))
    max_journey_age_h: float = field(default_factory=lambda: _f("BBB_MAX_JOURNEY_AGE_H", 2))
    # SIRI keeps re-broadcasting parked vehicles with old RecordedAtTime;
    # positions older than this are ghosts, not buses (the frozen-centre bug)
    max_recorded_age_s: float = field(default_factory=lambda: _f("BBB_MAX_RECORDED_AGE_S", 300))

    # Off by default: journey refs are often not real journey codes
    # (see matching.py docstring); enable as a deliberate, measured change
    enable_exact_match: bool = field(default_factory=lambda: os.getenv(
        "BBB_ENABLE_EXACT_MATCH", "0") == "1")

    def require_key(self) -> None:
        if not self.bods_api_key:
            raise SystemExit("BODS_API_KEY not set (put it in .env)")

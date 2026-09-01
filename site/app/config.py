"""Environment-driven site configuration."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SITE_DIR = Path(__file__).resolve().parent.parent
CARTO_BASEMAP_KEY_RE = re.compile(r"[A-Za-z0-9._~-]{16,512}")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {
        "1", "true", "yes", "on"
    }


@dataclass
class Config:
    live_db: str = field(default_factory=lambda: os.getenv("BBB_LIVE_DB", "live.db"))
    timetable_db: str = field(default_factory=lambda: os.getenv(
        "BBB_TIMETABLE_DB", "timetable.db"))
    fleet_json: str = field(default_factory=lambda: os.getenv(
        "BBB_FLEET_JSON", str(SITE_DIR / "fbribuses.json")))
    descriptions_json: str = field(default_factory=lambda: os.getenv(
        "BBB_DESCRIPTIONS_JSON", str(SITE_DIR / "bus-descriptions.json")))
    waiting_json: str = field(default_factory=lambda: os.getenv(
        "BBB_WAITING_JSON", str(SITE_DIR / "waiting-descriptions.json")))
    depot_descriptions_json: str = field(default_factory=lambda: os.getenv(
        "BBB_DEPOT_DESCRIPTIONS_JSON", str(SITE_DIR / "depot-descriptions.json")))
    boundary_geojson: str = field(default_factory=lambda: os.getenv(
        "BBB_BOUNDARY", str(SITE_DIR / "weca_boundary.geojson")))
    localities_json: str = field(default_factory=lambda: os.getenv(
        "BBB_LOCALITIES_JSON", str(SITE_DIR / "stop_localities.json")))
    enrichment_json: str = field(default_factory=lambda: os.getenv(
        "BBB_ENRICHMENT_JSON", str(SITE_DIR / "stop_enrichment.json")))
    audit_integration_json: str = field(default_factory=lambda: os.getenv(
        "BBB_AUDIT_INTEGRATION_JSON",
        "/var/lib/bristolbusbot/pipeline/audit_site/audit_integration.json"))
    audit_max_age_seconds: int = field(default_factory=lambda: int(
        os.getenv("BBB_AUDIT_MAX_AGE_S", "172800")))
    stale_vehicle_seconds: int = field(default_factory=lambda: int(
        os.getenv("BBB_STALE_VEHICLE_S", "90")))
    bot_recent_posts_url: str = field(default_factory=lambda: os.getenv(
        "BBB_BOT_RECENT_POSTS_URL",
        "http://127.0.0.1:3010/api/recent-posts?limit=20"))
    featured_post_max_age_minutes: int = field(default_factory=lambda: int(
        os.getenv("BBB_FEATURED_POST_MAX_AGE_MIN", "240")))
    enforce_https: bool = field(default_factory=lambda: _env_bool(
        "BBB_ENFORCE_HTTPS", False))
    carto_basemap_key: str = field(default_factory=lambda: os.getenv(
        "BBB_CARTO_BASEMAP_KEY", "").strip())
    public_hosts: tuple[str, ...] = ("bristolbuses.live", "www.bristolbuses.live")


def validate_carto_basemap_config(config: Config) -> None:
    """Reject missing production or malformed CARTO configuration safely."""
    key = config.carto_basemap_key
    if config.enforce_https and not key:
        raise RuntimeError("production CARTO basemap key is not configured")
    if key and not CARTO_BASEMAP_KEY_RE.fullmatch(key):
        raise RuntimeError("CARTO basemap key is invalid (value hidden)")

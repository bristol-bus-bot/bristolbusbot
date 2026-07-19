"""Defensive reader for the nightly, successfully published audit snapshot."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


logger = logging.getLogger(__name__)
_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def _identity(value: str | None) -> str:
    return _NON_ALNUM.sub("", str(value or "").upper())


class AuditIntegration:
    def __init__(self, path: str, max_age_seconds: int = 172800):
        self.path = Path(path)
        self.max_age = timedelta(seconds=max_age_seconds)
        self._mtime_ns: int | None = None
        self._payload: dict | None = None
        self._profiles: dict[str, dict] = {}
        self._by_vehicle: dict[tuple[str, str], str] = {}
        self._by_identity: dict[str, str] = {}

    def _reload_if_needed(self) -> None:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._clear()
            return
        if mtime_ns == self._mtime_ns:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema") != 1 or not payload.get("published_at"):
                raise ValueError("unsupported or unpublished snapshot")
            profiles = payload.get("profiles")
            if not isinstance(profiles, list):
                raise ValueError("profiles must be a list")
            by_slug: dict[str, dict] = {}
            by_vehicle: dict[tuple[str, str], str] = {}
            identity_candidates: dict[str, set[str]] = {}
            for profile in profiles:
                slug = str(profile.get("slug") or "")
                operator = str(profile.get("operator") or "")
                vehicle_ref = str(profile.get("vehicle_ref") or "")
                if not slug or not operator or not vehicle_ref:
                    continue
                by_slug[slug] = profile
                by_vehicle[(operator, vehicle_ref)] = slug
                for raw in (vehicle_ref, vehicle_ref.split("-")[-1]):
                    key = _identity(raw)
                    if key:
                        identity_candidates.setdefault(key, set()).add(slug)
            # Fleet numbers can be reused by different operators.  Only make
            # an identity shortcut when it resolves to exactly one profile.
            by_identity = {
                key: next(iter(slugs))
                for key, slugs in identity_candidates.items()
                if len(slugs) == 1
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("audit integration ignored: %s", exc)
            self._clear(mtime_ns)
            return
        self._mtime_ns = mtime_ns
        self._payload = payload
        self._profiles = by_slug
        self._by_vehicle = by_vehicle
        self._by_identity = by_identity

    def _clear(self, mtime_ns: int | None = None) -> None:
        self._mtime_ns = mtime_ns
        self._payload = None
        self._profiles = {}
        self._by_vehicle = {}
        self._by_identity = {}

    def snapshot(self, now: datetime | None = None) -> dict | None:
        self._reload_if_needed()
        if self._payload is None:
            return None
        try:
            published = datetime.fromisoformat(
                self._payload["published_at"].replace("Z", "+00:00"))
            if published.tzinfo is None:
                raise ValueError("publish time has no timezone")
        except (KeyError, TypeError, ValueError):
            return None
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age = current - published.astimezone(timezone.utc)
        if age < timedelta(0) or age > self.max_age:
            return None
        return self._payload

    def headline(self, now: datetime | None = None) -> dict | None:
        payload = self.snapshot(now)
        if payload is None:
            return None
        headline = payload.get("headline") or {}
        if not headline.get("eligible"):
            return None
        minimum = int(headline.get("minimum_readings") or 30)
        if int(headline.get("readings") or 0) < minimum:
            return None
        return headline

    def profile(self, slug: str, now: datetime | None = None) -> dict | None:
        if self.snapshot(now) is None:
            return None
        return self._profiles.get(slug)

    def slug_for_vehicle(self, operator: str | None,
                         vehicle_ref: str | None,
                         now: datetime | None = None) -> str | None:
        if self.snapshot(now) is None:
            return None
        return self._by_vehicle.get((str(operator or ""), str(vehicle_ref or "")))

    def slug_for_identity(self, *values: str | None,
                          now: datetime | None = None) -> str | None:
        if self.snapshot(now) is None:
            return None
        for value in values:
            slug = self._by_identity.get(_identity(value))
            if slug:
                return slug
        return None

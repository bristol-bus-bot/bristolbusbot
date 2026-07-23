#!/usr/bin/env python3
"""Validate the small, human-approved Bristol Bus Bot editorial context."""
from __future__ import annotations

import hashlib
import argparse
import json
import re
from datetime import date, datetime, timezone
from typing import Any
from pathlib import Path
from urllib.parse import urlsplit


MAX_BYTES = 256 * 1024
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,79}")
ALLOWED_SOURCE_HOSTS = {
    "bristolmuseums.org.uk",
    "bususers.org",
    "firstbus.co.uk",
    "firstgroupplc.com",
    "gov.uk",
    "legislation.gov.uk",
    "mobilityweek.eu",
    "tfl.gov.uk",
    "un.org",
}


class EditorialValidationError(ValueError):
    """The proposed editorial file does not satisfy its safety contract."""


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EditorialValidationError(f"{name} must be an object")
    return value


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise EditorialValidationError(
            f"{name} must be non-empty and at most {maximum} characters")
    if any(ord(character) < 32 and character not in "\t\n\r"
           for character in value):
        raise EditorialValidationError(f"{name} contains control characters")
    return value


def _date(value: Any, name: str) -> str:
    result = _text(value, name, 10)
    try:
        if date.fromisoformat(result).isoformat() != result:
            raise ValueError
    except ValueError as exc:
        raise EditorialValidationError(f"{name} must be an ISO date") from exc
    return result


def _timestamp(value: Any, name: str) -> str:
    result = _text(value, name, 40)
    if not result.endswith("Z"):
        raise EditorialValidationError(f"{name} must be a UTC ISO timestamp")
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EditorialValidationError(
            f"{name} must be a UTC ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise EditorialValidationError(f"{name} must be a UTC ISO timestamp")
    return result


def _number(value: Any, name: str, minimum: float, maximum: float,
            *, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not minimum <= value <= maximum:
        raise EditorialValidationError(
            f"{name} must be between {minimum} and {maximum}")
    if integer and not isinstance(value, int):
        raise EditorialValidationError(f"{name} must be an integer")
    return value


def _source(value: Any, name: str) -> dict[str, Any]:
    source = _mapping(value, name)
    url = _text(source.get("url"), f"{name}.url", 500)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(
            host == allowed or host.endswith(f".{allowed}")
            for allowed in ALLOWED_SOURCE_HOSTS):
        raise EditorialValidationError(
            f"{name}.url is not an allowlisted HTTPS source")
    result = {
        "publisher": _text(source.get("publisher"), f"{name}.publisher", 100),
        "title": _text(source.get("title"), f"{name}.title", 200),
        "url": url,
        "verified_on": _date(source.get("verified_on"), f"{name}.verified_on"),
    }
    if "published_on" in source:
        result["published_on"] = _date(
            source["published_on"], f"{name}.published_on")
    return result


def _identifier(value: Any, name: str, identifiers: set[str]) -> str:
    result = _text(value, name, 80)
    if not ID_RE.fullmatch(result):
        raise EditorialValidationError(f"{name} is not a safe identifier")
    if result in identifiers:
        raise EditorialValidationError(f"duplicate editorial id: {result}")
    identifiers.add(result)
    return result


def validate_document(value: Any) -> dict[str, Any]:
    root = _mapping(value, "editorial context")
    if root.get("schema_version") != 1:
        raise EditorialValidationError("unsupported editorial schema_version")
    updated_at = _timestamp(root.get("updated_at"), "updated_at")
    if "bee network" in json.dumps(root, ensure_ascii=False).lower():
        raise EditorialValidationError(
            "Bee Network claims are intentionally prohibited")

    arrays: dict[str, list[Any]] = {}
    for name, maximum in (("facts", 100), ("occasions", 100), ("news", 50)):
        items = root.get(name)
        if not isinstance(items, list) or len(items) > maximum:
            raise EditorialValidationError(
                f"{name} must be an array of at most {maximum} items")
        arrays[name] = items

    identifiers: set[str] = set()
    facts: list[dict[str, Any]] = []
    for index, raw in enumerate(arrays["facts"]):
        name = f"facts[{index}]"
        item = _mapping(raw, name)
        active_from = _date(item.get("active_from"), f"{name}.active_from")
        active_until = _date(item.get("active_until"), f"{name}.active_until")
        if active_until < active_from:
            raise EditorialValidationError(f"{name} has an inverted active window")
        facts.append({
            "id": _identifier(item.get("id"), f"{name}.id", identifiers),
            "claim": _text(item.get("claim"), f"{name}.claim", 600),
            "prompt_hint": _text(
                item.get("prompt_hint"), f"{name}.prompt_hint", 700),
            "active_from": active_from,
            "active_until": active_until,
            "source": _source(item.get("source"), f"{name}.source"),
        })

    occasions: list[dict[str, Any]] = []
    for index, raw in enumerate(arrays["occasions"]):
        name = f"occasions[{index}]"
        item = _mapping(raw, name)
        schedule = _mapping(item.get("schedule"), f"{name}.schedule")
        kind = schedule.get("kind")
        if kind == "annual_date":
            month = _number(
                schedule.get("month"), f"{name}.schedule.month", 1, 12,
                integer=True)
            day = _number(
                schedule.get("day"), f"{name}.schedule.day", 1, 31,
                integer=True)
            try:
                date(2024, month, day)
            except ValueError as exc:
                raise EditorialValidationError(
                    f"{name} has an invalid annual date") from exc
            parsed_schedule = {
                "kind": "annual_date", "month": month, "day": day}
        elif kind == "date_range":
            start = _date(schedule.get("start"), f"{name}.schedule.start")
            end = _date(schedule.get("end"), f"{name}.schedule.end")
            if end < start:
                raise EditorialValidationError(
                    f"{name} has an inverted date range")
            parsed_schedule = {"kind": "date_range", "start": start, "end": end}
        else:
            raise EditorialValidationError(
                f"{name}.schedule.kind is unsupported")
        occasions.append({
            "id": _identifier(item.get("id"), f"{name}.id", identifiers),
            "label": _text(item.get("label"), f"{name}.label", 120),
            "prompt_hint": _text(
                item.get("prompt_hint"), f"{name}.prompt_hint", 700),
            "schedule": parsed_schedule,
            "probability": _number(
                item.get("probability"), f"{name}.probability", 0, 1),
            "max_uses_per_day": _number(
                item.get("max_uses_per_day"), f"{name}.max_uses_per_day",
                1, 5, integer=True),
            "source": _source(item.get("source"), f"{name}.source"),
        })

    news: list[dict[str, Any]] = []
    for index, raw in enumerate(arrays["news"]):
        name = f"news[{index}]"
        item = _mapping(raw, name)
        published_at = _timestamp(
            item.get("published_at"), f"{name}.published_at")
        active_from = _timestamp(
            item.get("active_from"), f"{name}.active_from")
        expires_at = _timestamp(
            item.get("expires_at"), f"{name}.expires_at")
        if expires_at <= active_from or published_at > expires_at:
            raise EditorialValidationError(f"{name} has an invalid active window")
        append_source_link = item.get("append_source_link")
        if not isinstance(append_source_link, bool):
            raise EditorialValidationError(
                f"{name}.append_source_link must be boolean")
        source = _source(item.get("source"), f"{name}.source")
        if append_source_link and len(source["url"]) > 160:
            raise EditorialValidationError(
                f"{name}.source.url is too long to append safely")
        news.append({
            "id": _identifier(item.get("id"), f"{name}.id", identifiers),
            "label": _text(item.get("label"), f"{name}.label", 120),
            "claim": _text(item.get("claim"), f"{name}.claim", 800),
            "prompt_hint": _text(
                item.get("prompt_hint"), f"{name}.prompt_hint", 800),
            "published_at": published_at,
            "active_from": active_from,
            "expires_at": expires_at,
            "probability": _number(
                item.get("probability"), f"{name}.probability", 0, 1),
            "max_uses_total": _number(
                item.get("max_uses_total"), f"{name}.max_uses_total",
                1, 10, integer=True),
            "cooldown_hours": _number(
                item.get("cooldown_hours"), f"{name}.cooldown_hours", 1, 720),
            "append_source_link": append_source_link,
            "source": source,
        })

    return {
        "schema_version": 1,
        "updated_at": updated_at,
        "facts": facts,
        "occasions": occasions,
        "news": news,
    }


def validate_bytes(raw: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    if not raw or len(raw) > MAX_BYTES:
        raise EditorialValidationError(
            f"editorial context must be between 1 and {MAX_BYTES} bytes")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EditorialValidationError(
            "editorial context is not valid UTF-8 JSON") from exc
    document = validate_document(value)
    summary = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "updated_at": document["updated_at"],
        "facts": len(document["facts"]),
        "occasions": len(document["occasions"]),
        "news": len(document["news"]),
    }
    return document, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    args = parser.parse_args(argv)
    _, summary = validate_bytes(args.file.read_bytes())
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

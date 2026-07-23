#!/usr/bin/env python3
"""Propose one recent official GOV.UK bus story for human GitHub approval."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


SEARCH_URL = "https://www.gov.uk/api/search.json?" + urllib.parse.urlencode({
    "count": "50",
    "order": "-public_timestamp",
    "filter_organisations": "department-for-transport",
})
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
BUS_RE = re.compile(r"\b(bus|buses|coach|coaches)\b", re.IGNORECASE)
ID_PART_RE = re.compile(r"[^a-z0-9]+")
ALLOWED_NEWS_FORMATS = {"news_story", "press_release"}


class NoNewsCandidate(RuntimeError):
    """No recent, relevant, not-already-reviewed story was available."""


class NewsDiscoveryError(RuntimeError):
    """The source or local context could not be safely processed."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fetch_search(opener=None, sleeper: Callable[[float], None] = time.sleep) -> dict:
    opener = opener or urllib.request.build_opener()
    request = urllib.request.Request(
        SEARCH_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "bristolbusbot-editorial-discovery/1",
        },
    )
    for attempt in range(1, 4):
        try:
            with opener.open(request, timeout=30) as response:
                if response.geturl().split("?", 1)[0] != SEARCH_URL.split("?", 1)[0]:
                    raise NewsDiscoveryError(
                        "GOV.UK search redirected to an unexpected endpoint")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if not raw or len(raw) > MAX_RESPONSE_BYTES:
                    raise NewsDiscoveryError(
                        "GOV.UK search response has an unsafe size")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise NewsDiscoveryError(
                        "GOV.UK search response is not an object")
                return value
        except NewsDiscoveryError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                sleeper(2 ** (attempt - 1))
                continue
            raise NewsDiscoveryError(
                f"GOV.UK search returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError) as exc:
            if attempt < 3:
                sleeper(2 ** (attempt - 1))
                continue
            raise NewsDiscoveryError(
                "GOV.UK search could not be read after three attempts") from exc
    raise AssertionError("unreachable")


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def source_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def safe_id(title: str, url: str) -> str:
    slug = ID_PART_RE.sub("-", title.lower()).strip("-")[:50].rstrip("-")
    return f"govuk-{slug}-{source_id(url)[:8]}"


def select_candidate(
    search: dict,
    context: dict,
    *,
    now: datetime,
    excluded_source_ids: set[str] | None = None,
) -> dict:
    results = search.get("results")
    if not isinstance(results, list):
        raise NewsDiscoveryError("GOV.UK search results are missing")
    known_urls = {
        item.get("source", {}).get("url")
        for collection in ("facts", "occasions", "news")
        for item in context.get(collection, [])
        if isinstance(item, dict) and isinstance(item.get("source"), dict)
    }
    oldest = now - timedelta(days=7)
    excluded_source_ids = excluded_source_ids or set()
    for raw in results:
        if not isinstance(raw, dict):
            continue
        title = raw.get("title")
        description = raw.get("description")
        link = raw.get("link")
        news_format = raw.get("format")
        if not all(isinstance(value, str) and value.strip()
                   for value in (title, description, link)):
            continue
        if news_format not in ALLOWED_NEWS_FORMATS \
                or not link.startswith("/government/news/"):
            continue
        combined = f"{title} {description}"
        if not BUS_RE.search(combined) or "bee network" in combined.lower():
            continue
        if not link.startswith("/") or "\\" in link or link.startswith("//"):
            continue
        url = f"https://www.gov.uk{link}"
        if url in known_urls or source_id(url) in excluded_source_ids:
            continue
        try:
            published = parse_timestamp(raw.get("public_timestamp"))
        except ValueError:
            continue
        if published < oldest or published > now + timedelta(minutes=5):
            continue
        claim = f"{title.rstrip('.')}. {description.strip()}"
        if len(claim) > 800:
            claim = claim[:797].rsplit(" ", 1)[0] + "..."
        expires = min(published + timedelta(days=7), now + timedelta(days=7))
        return {
            "id": safe_id(title, url),
            "source_id": source_id(url),
            "title": title.strip(),
            "url": url,
            "published": published,
            "expires": expires,
            "item": {
                "id": safe_id(title, url),
                "label": title.strip()[:120],
                "claim": claim,
                "prompt_hint": (
                    "This wording came from the approved GOV.UK title and summary. "
                    "Use exact dates, make no prediction, and do not add facts from "
                    "outside this claim."
                ),
                "published_at": published.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "active_from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "probability": 0.1,
                "max_uses_total": 2,
                "cooldown_hours": 36,
                "append_source_link": True,
                "source": {
                    "publisher": "UK Government",
                    "title": title.strip()[:200],
                    "url": url,
                    "published_on": published.date().isoformat(),
                    "verified_on": now.date().isoformat(),
                },
            },
        }
    raise NoNewsCandidate("no new official bus story needs review")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.new-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_github_output(path: Path, candidate: dict) -> None:
    values = {
        "id": candidate["id"],
        "source_id": candidate["source_id"],
        "title": candidate["title"].replace("\n", " ")[:120],
        "url": candidate["url"],
        "expires_at": candidate["expires"].strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--context", type=Path,
        default=Path("bot/data/editorial-context.json"))
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--excluded-source-ids", default="")
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    try:
        context = json.loads(args.context.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NewsDiscoveryError("local editorial context is unreadable") from exc
    now = parse_timestamp(args.now) if args.now else utcnow()
    try:
        candidate = select_candidate(
            fetch_search(),
            context,
            now=now,
            excluded_source_ids={
                value for value in args.excluded_source_ids.split(",")
                if re.fullmatch(r"[0-9a-f]{16}", value)
            },
        )
    except NoNewsCandidate as exc:
        print(str(exc))
        return 75
    context["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    context.setdefault("news", []).append(candidate["item"])
    atomic_json(args.context, context)
    if args.github_output:
        write_github_output(args.github_output, candidate)
    print(json.dumps({
        "status": "proposed",
        "id": candidate["id"],
        "url": candidate["url"],
        "expires_at": candidate["expires"].isoformat(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

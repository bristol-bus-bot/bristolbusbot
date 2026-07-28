#!/usr/bin/env python3
"""Produce a read-only Threads shadow report from successful Bluesky posts.

This script never calls Meta and never writes to the bot database. It answers
"what would the selector have chosen?" with an explicit reason for every row.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


LONDON = ZoneInfo("Europe/London")
REQUIRED_COLUMNS = {
    "operator_ref", "vehicle_ref", "line", "journey_ref",
    "origin_aimed_departure", "delay_seconds", "low_confidence",
    "post_content", "post_type", "significance_score", "timestamp", "post_uri",
}


@dataclass(frozen=True)
class Rules:
    minimum_significance: int = 7
    daily_budget: int = 8
    hard_ceiling: int = 15
    route_cooldown_minutes: int = 180
    candidate_lifetime_minutes: int = 60
    overnight_severe_delay_minutes: int = 20

    def validated(self) -> "Rules":
        if not 0 <= self.daily_budget <= self.hard_ceiling <= 15:
            raise ValueError("daily budget must be between 0 and the hard ceiling (maximum 15)")
        if self.route_cooldown_minutes < 0 or self.candidate_lifetime_minutes < 1:
            raise ValueError("cooldown must be non-negative and lifetime must be positive")
        return self


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def read_posts(path: Path, since: datetime) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(engagement_analytics)")
        }
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise RuntimeError(
                "engagement database predates social provenance columns: "
                + ", ".join(missing))
        rows = connection.execute(
            """SELECT operator_ref, vehicle_ref, line, journey_ref,
                      origin_aimed_departure, delay_seconds, low_confidence,
                      post_content, post_type, significance_score, timestamp,
                      post_uri
                 FROM engagement_analytics
                WHERE post_uri IS NOT NULL AND timestamp >= ?
                ORDER BY timestamp ASC, id ASC""",
            (since.isoformat(),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _base_rejection(row: dict, rules: Rules) -> str | None:
    identity = [row.get(field) for field in (
        "operator_ref", "vehicle_ref", "line", "journey_ref",
        "origin_aimed_departure")]
    if not all(str(value or "").strip() for value in identity):
        return "missing exact journey provenance"
    if bool(row.get("low_confidence")):
        return "low-confidence event"
    if int(row.get("significance_score") or 0) < rules.minimum_significance:
        return f"significance below {rules.minimum_significance}"
    posted = _timestamp(row["timestamp"])
    if posted.astimezone(LONDON).hour < 6:
        delay = abs(int(row.get("delay_seconds") or 0))
        if delay < rules.overnight_severe_delay_minutes * 60:
            return f"overnight delay below {rules.overnight_severe_delay_minutes} minutes"
    return None


def select(posts: list[dict], rules: Rules) -> dict:
    rules.validated()
    decisions = []
    eligible_by_day: dict[str, list[dict]] = {}
    for row in posts:
        posted = _timestamp(row["timestamp"])
        item = {
            "sourceBlueskyUri": row.get("post_uri"),
            "postText": row.get("post_content"),
            "postedAt": posted.isoformat(),
            "expiresAt": (posted + timedelta(
                minutes=rules.candidate_lifetime_minutes)).isoformat(),
            "operator": row.get("operator_ref"),
            "route": row.get("line"),
            "vehicleRef": row.get("vehicle_ref"),
            "journeyRef": row.get("journey_ref"),
            "significance": int(row.get("significance_score") or 0),
            "delaySeconds": row.get("delay_seconds"),
            "decision": "rejected",
            "reason": None,
        }
        rejection = _base_rejection(row, rules)
        if rejection:
            item["reason"] = rejection
            decisions.append(item)
            continue
        day = posted.astimezone(LONDON).date().isoformat()
        eligible_by_day.setdefault(day, []).append({"row": row, "item": item})

    for day, eligible in sorted(eligible_by_day.items()):
        ranked = sorted(eligible, key=lambda entry: (
            -entry["item"]["significance"], entry["item"]["postedAt"]))
        selected: list[dict] = []
        for entry in ranked:
            item = entry["item"]
            posted = _timestamp(item["postedAt"])
            collision = next((prior for prior in selected
                              if prior["operator"] == item["operator"]
                              and prior["route"] == item["route"]
                              and abs((_timestamp(prior["postedAt"]) - posted).total_seconds())
                              < rules.route_cooldown_minutes * 60), None)
            if collision:
                item["reason"] = (
                    f"same operator and route within {rules.route_cooldown_minutes} minutes "
                    f"of higher-ranked candidate")
            elif len(selected) >= rules.daily_budget:
                item["reason"] = f"outside daily significance budget of {rules.daily_budget}"
            else:
                item["decision"] = "candidate"
                item["reason"] = "selected by significance budget"
                selected.append(item)
            decisions.append(item)

    decisions.sort(key=lambda item: item["postedAt"])
    return {
        "schema": 1,
        "mode": "shadow-only",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "rules": asdict(rules),
        "summary": {
            "examined": len(decisions),
            "candidates": sum(item["decision"] == "candidate" for item in decisions),
            "rejected": sum(item["decision"] == "rejected" for item in decisions),
        },
        "decisions": decisions,
    }


def markdown(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "# Threads shadow candidates",
        "",
        "No posts were published. This is a read-only selector report.",
        "",
        f"Examined: {summary['examined']} | candidates: {summary['candidates']} | rejected: {summary['rejected']}",
        "",
    ]
    for item in report["decisions"]:
        mark = "CANDIDATE" if item["decision"] == "candidate" else "REJECTED"
        lines.extend([
            f"## {mark} | {item['postedAt']} | route {item['route'] or 'unknown'}",
            "",
            f"Significance {item['significance']} | {item['reason']}",
            "",
            str(item["postText"] or ""),
            "",
        ])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--lookback-hours", type=int, default=24)
    parser.add_argument("--minimum-significance", type=int, default=7)
    parser.add_argument("--daily-budget", type=int, default=8)
    parser.add_argument("--hard-ceiling", type=int, default=15)
    parser.add_argument("--now", help="fixed ISO time for testing")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    args = parser.parse_args(argv)
    now = _timestamp(args.now) if args.now else datetime.now(timezone.utc)
    rules = Rules(minimum_significance=args.minimum_significance,
                  daily_budget=args.daily_budget,
                  hard_ceiling=args.hard_ceiling).validated()
    report = select(read_posts(args.db, now - timedelta(
        hours=max(1, args.lookback_hours))), rules)
    rendered = markdown(report)
    if args.json_output:
        args.json_output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.markdown_output:
        args.markdown_output.write_text(rendered, encoding="utf-8")
    if not args.json_output and not args.markdown_output:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

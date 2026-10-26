"""Measure which disruptions the stop notices can and cannot show (#76).

Run on a laptop COPY of live.db, never against the Pi's live file:

    python site/scripts/notice_gaps.py path/to/live-copy.db --days 28

It reports, for disruptions seen in the window (open or since closed):
- how many each publisher sent, and how many of those were roadworks
- how many list at least one affected stop (the only way a stop panel matches)
- how many mention a stop only in their text, with the list for a human to read
- how many the stop panel would skip for missing or uncertain dates

Text mentions are listed for the maintainer only. They are never shown on the
site, because a word match cannot say which stop or whether it is closed.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.notices import _date, _window_status  # noqa: E402

STOP_WORDS = re.compile(r"\b(stops?|bus stop|stand|shelter)\b", re.I)


def measure(conn, *, days=28, now=None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT situation_number, participant, progress, reason, summary,
                  description, versioned_at, affected_json, closed_at
           FROM situations WHERE COALESCE(versioned_at, updated_at) >= ?""",
        (since,)).fetchall()
    publishers = defaultdict(Counter)
    text_only = []
    for r in rows:
        who = publishers[r["participant"] or "unknown"]
        who["total"] += 1
        if (r["reason"] or "").lower() == "roadworks":
            who["roadworks"] += 1
        try:
            affected = json.loads(r["affected_json"] or "{}")
        except (ValueError, TypeError):
            affected = {}
        affected = affected if isinstance(affected, dict) else {}
        stops = [s for s in affected.get("stops", []) if isinstance(s, dict) and s.get("stop_ref")]
        if stops:
            who["with_stops"] += 1
        text = f"{r['summary'] or ''}\n{r['description'] or ''}"
        if not stops and STOP_WORDS.search(text):
            who["stop_in_text_only"] += 1
            text_only.append({"id": r["situation_number"], "publisher": r["participant"],
                              "summary": (r["summary"] or "")[:120]})
        periods = affected.get("validity_periods")
        if not isinstance(periods, list):
            who["skipped_no_dates"] += 1
        else:
            seen_at = _date(r["versioned_at"]) or now
            status, _ = _window_status(periods, r["description"] or "", seen_at)
            if status == "uncertain":
                who["uncertain_dates"] += 1
    return {"days": days, "situations": len(rows),
            "publishers": {k: dict(v) for k, v in sorted(publishers.items())},
            "stop_in_text_only": text_only}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db", help="a laptop copy of live.db")
    parser.add_argument("--days", type=int, default=28)
    args = parser.parse_args(argv)
    conn = sqlite3.connect(f"file:{Path(args.db).resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    print(json.dumps(measure(conn, days=args.days), indent=2))


if __name__ == "__main__":
    main()

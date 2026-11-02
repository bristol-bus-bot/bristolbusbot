#!/usr/bin/env python3
"""Give every stop in a timetable candidate its best public name, once.

The bot, the site, the live map and the audit all read stop names from the
timetable, so correcting them here is the only place it needs doing. The
corrections live in stop_names.json beside this file. The timetable's
``stop_name`` holds the corrected name and ``source_stop_name`` the name as
published, so running this again starts from the original each time.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

CORRECTIONS = Path(__file__).resolve().parent / "stop_names.json"


class StopNames:
    def __init__(self, path: Path = CORRECTIONS):
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        self.exact = {(code.lower(), name): cleaned for code, name, cleaned in doc["exact"]}
        self.prefix = [(prefix.lower(), name, cleaned) for prefix, name, cleaned in doc["prefix"]]
        self.any = dict(doc["any"])
        self.words = [(re.compile(rf"\b{re.escape(short)}\b"), long) for short, long in doc["words"]]

    def best(self, stop_code: str | None, stop_name: str | None) -> str | None:
        """The public name for a stop as published in the timetable."""
        if not stop_name:
            return stop_name
        name = stop_name.strip()
        code = (stop_code or "").strip().lower()
        if (code, name) in self.exact:
            return self.exact[(code, name)]
        for prefix, original, cleaned in self.prefix:
            if code and code.startswith(prefix) and name == original:
                return cleaned
        if name in self.any:
            return self.any[name]
        for pattern, long in self.words:
            name = pattern.sub(long, name)
        return name


def prepare_stop_names(database: Path, corrections: Path = CORRECTIONS) -> int:
    """Rewrite stop_name from source_stop_name; returns how many stops changed."""
    names = StopNames(corrections)
    connection = sqlite3.connect(database)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(stops)")}
        if "source_stop_name" not in columns:
            connection.execute("ALTER TABLE stops ADD COLUMN source_stop_name TEXT")
            connection.execute("UPDATE stops SET source_stop_name = stop_name")
        rows = connection.execute("SELECT stop_id, stop_code, source_stop_name FROM stops").fetchall()
        changed = 0
        for stop_id, code, source in rows:
            best = names.best(code, source)
            changed += best != source
            connection.execute("UPDATE stops SET stop_name = ? WHERE stop_id = ?", (best, stop_id))
        connection.commit()
        return changed
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    changed = prepare_stop_names(args.database)
    print(f"Stop names: {changed} corrected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

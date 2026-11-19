"""#113: the poll-record prune uses its index and deletes the same rows."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import audit_rollup  # noqa: E402


def test_prune_uses_the_index_and_deletes_exactly_the_old_days():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE timepoint_observations (service_date TEXT, trip_id TEXT, stop_sequence INTEGER,
                                             PRIMARY KEY (service_date, trip_id, stop_sequence));
        CREATE TABLE poll_log (poll_at TEXT PRIMARY KEY, ok INTEGER);
    """)
    polls = ["2026-08-10T23:59:59.999999+00:00", "2026-08-11T00:00:00+00:00",
             "2026-08-11T12:00:00.5+00:00", "2026-08-12T00:00:00+00:00"]
    conn.executemany("INSERT INTO poll_log VALUES (?, 1)", [(p,) for p in polls])
    conn.executemany("INSERT INTO timepoint_observations VALUES (?, 't', 1)",
                     [("20260810",), ("20260811",), ("20260812",)])
    old_way = conn.execute("SELECT poll_at FROM poll_log WHERE substr(poll_at,1,10) < '2026-08-12' "
                           "ORDER BY poll_at").fetchall()

    assert audit_rollup.prune_old_raw(conn, "20260812") == 2

    kept = [row[0] for row in conn.execute("SELECT poll_at FROM poll_log")]
    assert kept == ["2026-08-12T00:00:00+00:00"]
    assert [row[0] for row in old_way] == polls[:3]          # the same rows as before
    plan = conn.execute("EXPLAIN QUERY PLAN DELETE FROM poll_log WHERE poll_at < ?",
                        ("2026-08-12",)).fetchall()
    assert "USING" in plan[0][3] and "INDEX" in plan[0][3] and "SCAN" not in plan[0][3]
    source = Path(audit_rollup.__file__).read_text(encoding="utf-8")
    assert "substr(poll_at" not in source

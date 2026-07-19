from datetime import datetime
import sqlite3
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import status_digest


def test_bot_digest_counts_durable_success_rows_only(tmp_path, monkeypatch):
    today = datetime.now().strftime("%Y-%m-%d")
    database = tmp_path / "app_data.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE engagement_analytics (timestamp TEXT, post_uri TEXT)")
    connection.executemany(
        "INSERT INTO engagement_analytics VALUES (?, ?)",
        ((f"{today}T08:00:00+01:00", "at://successful"),
         (f"{today}T08:05:00+01:00", None),
         ("2020-01-01T00:00:00+00:00", "at://old")),
    )
    connection.commit()
    connection.close()
    monkeypatch.setattr(status_digest, "BOT_DB", database)
    assert "1 post(s)" in status_digest.bot_line()

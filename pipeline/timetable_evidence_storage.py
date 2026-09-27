"""Intern repeated correction proofs in a finished, read-only timetable.

Compatibility views retain every original receipt column and JSON byte. This
changes storage only, never the calendars or decisions made by reconciliation.
"""
import sqlite3


TABLES = ('duplicate_source_corrections', 'calendar_nonoperation_corrections')


def compact_evidence(conn: sqlite3.Connection) -> None:
    with conn:
        for table in TABLES:
            kind = conn.execute(
                'SELECT type FROM sqlite_master WHERE name=?', (table,)
            ).fetchone()
            if kind is None or kind[0] == 'view':
                continue
            proofs = table + '_proofs'
            receipts = table + '_receipts'
            conn.execute(f'''CREATE TABLE {proofs} (
                evidence_id INTEGER PRIMARY KEY,
                evidence_json TEXT NOT NULL UNIQUE)''')
            conn.execute(f'''INSERT INTO {proofs}(evidence_json)
                SELECT DISTINCT evidence_json FROM {table}''')
            conn.execute(f'''CREATE TABLE {receipts} (
                trip_id TEXT NOT NULL, date TEXT NOT NULL,
                original_service_id TEXT NOT NULL,
                corrected_service_id TEXT NOT NULL,
                evidence_id INTEGER NOT NULL REFERENCES {proofs}(evidence_id),
                PRIMARY KEY(trip_id,date)) WITHOUT ROWID''')
            conn.execute(f'''INSERT INTO {receipts}
                SELECT t.trip_id,t.date,t.original_service_id,
                       t.corrected_service_id,p.evidence_id
                FROM {table} t JOIN {proofs} p USING(evidence_json)''')
            old_count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            new_count = conn.execute(f'SELECT COUNT(*) FROM {receipts}').fetchone()[0]
            if old_count != new_count:
                raise RuntimeError('correction evidence compaction lost receipts')
            conn.execute(f'DROP TABLE {table}')
            conn.execute(f'''CREATE VIEW {table} AS
                SELECT r.trip_id,r.date,r.original_service_id,
                       r.corrected_service_id,p.evidence_json
                FROM {receipts} r JOIN {proofs} p USING(evidence_id)''')

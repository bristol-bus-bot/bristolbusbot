import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from timetable_evidence_storage import compact_evidence, TABLES


def test_compaction_preserves_all_receipts_and_shares_identical_proofs():
    conn = sqlite3.connect(':memory:')
    originals = {}
    for table in TABLES:
        conn.execute(f'''CREATE TABLE {table} (
            trip_id TEXT,date TEXT,original_service_id TEXT,
            corrected_service_id TEXT,evidence_json TEXT,
            PRIMARY KEY(trip_id,date))''')
        rows = [('a','20260901','old','new','{"proof": "same"}'),
                ('a','20260902','old','new','{"proof": "same"}'),
                ('b','20260901','old2','new2','{"proof": "different"}')]
        conn.executemany(f'INSERT INTO {table} VALUES (?,?,?,?,?)', rows)
        originals[table] = rows
    conn.commit()
    compact_evidence(conn)
    compact_evidence(conn)  # Finalization can safely be repeated.
    for table in TABLES:
        assert conn.execute(f'SELECT * FROM {table} ORDER BY trip_id,date').fetchall() == originals[table]
        assert conn.execute(f'SELECT COUNT(*) FROM {table}_proofs').fetchone()[0] == 2
    assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_absent_receipt_tables_are_allowed():
    compact_evidence(sqlite3.connect(':memory:'))

import sys
from pathlib import Path


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_export  # noqa: E402
import audit_rollup  # noqa: E402


def test_rollup_returns_failure_when_audit_database_is_missing(
        monkeypatch, tmp_path):
    monkeypatch.setattr(audit_rollup, "AUDIT_DB", str(tmp_path / "missing.db"))

    assert audit_rollup.main() == 1


def test_export_returns_failure_when_audit_database_is_missing(
        monkeypatch, tmp_path):
    monkeypatch.setattr(audit_export, "AUDIT_DB", str(tmp_path / "missing.db"))

    assert audit_export.main() == 1

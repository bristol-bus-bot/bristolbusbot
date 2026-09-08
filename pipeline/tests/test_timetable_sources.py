import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest
from types import SimpleNamespace as NS
from datetime import date, timedelta
import xml.etree.ElementTree as ET


PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audit_fetch_first_txc as first_txc  # noqa: E402
import audit_fetch_tnds as tnds  # noqa: E402
import audit_txc_to_timetable as txc_merge  # noqa: E402


def write_zip(path: Path, member: str = "source.xml") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(member, "<TransXChange />")


def test_source_errors_redact_credentials(monkeypatch):
    bods_key = "bods-secret-value"
    monkeypatch.setattr(first_txc, "API_KEY", bods_key)
    bods_error = RuntimeError(
        f"failed https://example.invalid/data?api_key={bods_key}&noc=FBRI")
    bods_message = first_txc.safe_error(bods_error)
    assert bods_key not in bods_message
    assert "api_key=[REDACTED]" in bods_message


def test_first_txc_remote_display_text_cannot_echo_credentials(monkeypatch):
    bods_key = "bods-secret-value"
    monkeypatch.setattr(first_txc, "API_KEY", bods_key)
    display = first_txc.safe_output(
        f"remote name\nhttps://example.invalid/?api_key={bods_key}")
    assert bods_key not in display
    assert "\n" not in display
    assert "api_key=[REDACTED]" in display


@pytest.mark.parametrize("value", [None, "", "../outside", "12/34", "123x"])
def test_first_txc_rejects_unsafe_remote_dataset_ids(value):
    with pytest.raises(ValueError, match="invalid dataset identifier"):
        first_txc.safe_dataset_id(value)


def test_tnds_errors_redact_username_and_password(monkeypatch):
    monkeypatch.setattr(tnds, "USER", "download-user")
    monkeypatch.setattr(tnds, "PASS", "download-password")
    message = tnds.safe_error(
        RuntimeError("download-user failed with download-password"))
    assert "download-user" not in message
    assert "download-password" not in message
    assert message.count("[REDACTED]") == 2


def test_tnds_download_resumes_after_a_stalled_data_connection(
        tmp_path, monkeypatch):
    source_zip = tmp_path / "source.zip"
    write_zip(source_zip)
    payload = source_zip.read_bytes()
    state = {"connections": 0, "starts": []}

    class FakeFTP:
        def __init__(self, **_kwargs):
            state["connections"] += 1
            self.connection = state["connections"]

        def cwd(self, _directory):
            pass

        def size(self, _name):
            return len(payload)

        def retrbinary(self, _command, callback, blocksize, rest=None):
            start = rest or 0
            state["starts"].append(start)
            remaining = payload[start:]
            if self.connection == 1:
                amount = max(1, len(remaining) // 2)
                callback(remaining[:amount])
                raise TimeoutError("simulated stalled transfer")
            for offset in range(0, len(remaining), blocksize):
                callback(remaining[offset:offset + blocksize])

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tnds, "USER", "user")
    monkeypatch.setattr(tnds, "PASS", "password")
    destination = tmp_path / "SW.zip"

    size = tnds.download_archive(
        destination, ftp_factory=FakeFTP, sleep=lambda _delay: None)

    assert size == len(payload)
    assert destination.read_bytes() == payload
    assert state["connections"] == 2
    assert state["starts"][0] == 0
    assert 0 < state["starts"][1] < len(payload)
    assert not destination.with_suffix(".zip.part").exists()


def test_tnds_failure_preserves_previous_archive(tmp_path, monkeypatch):
    class CorruptFTP:
        def __init__(self, **_kwargs):
            pass

        def cwd(self, _directory):
            pass

        def size(self, _name):
            return 7

        def retrbinary(self, _command, callback, blocksize, rest=None):
            callback(b"not-zip"[(rest or 0):])

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(tnds, "USER", "user")
    monkeypatch.setattr(tnds, "PASS", "password")
    destination = tmp_path / "SW.zip"
    destination.write_bytes(b"previous-valid-source")

    with pytest.raises(RuntimeError, match="after 2 attempts"):
        tnds.download_archive(
            destination, ftp_factory=CorruptFTP,
            sleep=lambda _delay: None, max_attempts=2)

    assert destination.read_bytes() == b"previous-valid-source"
    assert not destination.with_suffix(".zip.part").exists()


def test_first_txc_failure_preserves_previous_complete_cache(
        tmp_path, monkeypatch):
    destination = tmp_path / "first-txc"
    destination.mkdir()
    previous = destination / "fbri_previous.zip"
    write_zip(previous)

    monkeypatch.setattr(first_txc, "API_KEY", "test-key")
    monkeypatch.setattr(first_txc, "OUT_DIR", destination)
    monkeypatch.setattr(first_txc, "list_datasets", lambda _session: [
        {"id": 1, "name": "one", "url": "https://example.invalid/one"},
        {"id": 2, "name": "two", "url": "https://example.invalid/two"},
    ])

    def fail_second(_session, _url, output):
        if output.name == "fbri_2.zip":
            raise RuntimeError("simulated interrupted download")
        write_zip(output)
        return output.stat().st_size

    monkeypatch.setattr(first_txc, "download_archive", fail_second)

    assert first_txc.main() == 1
    assert previous.exists()
    assert sorted(path.name for path in destination.iterdir()) == [
        "fbri_previous.zip"
    ]
    assert not destination.with_name(
        f".{destination.name}.new-{first_txc.os.getpid()}").exists()


def test_first_txc_success_replaces_cache_as_one_complete_set(
        tmp_path, monkeypatch):
    destination = tmp_path / "first-txc"
    destination.mkdir()
    write_zip(destination / "stale.zip")

    monkeypatch.setattr(first_txc, "API_KEY", "test-key")
    monkeypatch.setattr(first_txc, "OUT_DIR", destination)
    monkeypatch.setattr(first_txc, "list_datasets", lambda _session: [
        {"id": 10, "name": "ten", "url": "https://example.invalid/ten"},
        {"id": 20, "name": "twenty", "url": "https://example.invalid/twenty"},
    ])

    def download(_session, _url, output):
        write_zip(output)
        return output.stat().st_size

    monkeypatch.setattr(first_txc, "download_archive", download)

    assert first_txc.main() == 0
    assert sorted(path.name for path in destination.iterdir()) == [
        "fbri_10.zip", "fbri_20.zip"
    ]


def test_txc_merge_rolls_back_when_any_required_archive_is_corrupt(
        tmp_path, monkeypatch):
    database = tmp_path / "timetable.db"
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE agency (agency_id TEXT PRIMARY KEY, agency_noc TEXT);
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY, agency_id TEXT, route_short_name TEXT,
            route_type INTEGER);
        CREATE TABLE stops (
            stop_id TEXT PRIMARY KEY, stop_code TEXT, stop_name TEXT,
            stop_lat REAL, stop_lon REAL);
        CREATE TABLE trips (
            trip_id TEXT PRIMARY KEY, route_id TEXT, service_id TEXT,
            direction_id INTEGER);
        CREATE TABLE stop_times (
            trip_id TEXT, arrival_time TEXT, departure_time TEXT,
            stop_id TEXT, stop_sequence INTEGER, timepoint INTEGER);
        CREATE TABLE calendar (
            service_id TEXT PRIMARY KEY, monday INTEGER, tuesday INTEGER,
            wednesday INTEGER, thursday INTEGER, friday INTEGER,
            saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT);
        INSERT INTO agency VALUES ('A', 'FBRI');
    """)
    connection.commit()
    connection.close()

    source = tmp_path / "first-txc"
    source.mkdir()
    (source / "corrupt.zip").write_bytes(b"not a zip")
    monkeypatch.setattr(sys, "argv", [
        "audit_txc_to_timetable.py", str(database), str(source)
    ])

    assert txc_merge.main() == 1
    check = sqlite3.connect(database)
    assert check.execute("SELECT COUNT(*) FROM routes").fetchone()[0] == 0
    check.close()


@pytest.mark.parametrize('reused_code', [False, True])
def test_txc_supplement_keeps_original_journey_code_for_source_reconciliation(tmp_path,monkeypatch,reused_code):
    database=tmp_path/'tt.db';c=sqlite3.connect(database)
    c.executescript('''CREATE TABLE agency(agency_id,agency_noc);
    CREATE TABLE routes(route_id,agency_id,route_short_name,route_type);
    CREATE TABLE trips(trip_id,route_id,service_id,direction_id);
    CREATE TABLE stops(stop_id,stop_code,stop_name,stop_lat,stop_lon);
    CREATE TABLE stop_times(trip_id,arrival_time,departure_time,stop_id,stop_sequence,timepoint);
    CREATE TABLE calendar(service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date);
    INSERT INTO agency VALUES('a','FBRI');
    INSERT INTO stops VALUES('A','A','A',51,-2),('B','B','B',51,-2);''')
    c.commit();c.close()
    source=tmp_path/'sources';source.mkdir()
    with zipfile.ZipFile(source/'one.zip','w') as z:
        z.writestr('one.xml','<FBRI><LineName>373</LineName></FBRI>')
        if reused_code:
            z.writestr('two.xml','<FBRI><LineName>373</LineName></FBRI>')
    profile=NS(regular_days=[NS(day=0)])
    cells=[NS(stopusage=NS(stop=NS(atco_code=stop),timingstatus='PTP'),
              arrival_time=timedelta(hours=8,minutes=i),departure_time=timedelta(hours=8,minutes=i))
           for i,stop in enumerate(['A','outside','B'])]
    journey=NS(code='VJ780',operating_profile=profile,journey_pattern=NS(is_inbound=lambda:False),get_times=lambda:cells)
    service=NS(operator='op',service_code='PH0000132:3',lines=[NS(line_name='373',id='L')],
               operating_profile=profile,operating_period=NS(start=date(2026,9,6),end=date(2026,9,30)))
    doc=NS(operators=[ET.fromstring('<Operator id="op"><NationalOperatorCode>FBRI</NationalOperatorCode></Operator>')],
           services={'S':service},get_journeys=lambda *_:[journey])
    import copy
    newer=copy.deepcopy(doc)
    newer.services['S'].operating_period.start=date(2026,9,13)
    parsed=iter([doc,newer])
    monkeypatch.setattr(txc_merge.txc,'TransXChange',lambda *_:next(parsed))
    monkeypatch.setattr(sys,'argv',['merge',str(database),str(source)])
    assert txc_merge.main()==0
    c=sqlite3.connect(database)
    expected=[('SUP_T_PH0000132:3_373_20260906_VJ780','VJ780')]
    if reused_code:expected.append(('SUP_T_PH0000132:3_373_20260913_VJ780','VJ780'))
    assert c.execute('SELECT trip_id,vehicle_journey_code FROM trips ORDER BY trip_id').fetchall()==expected
    assert c.execute('SELECT count(*) FROM stop_times').fetchone()[0]==2*len(expected)
    import json
    calls=json.loads(c.execute('SELECT full_calls_json FROM supplement_trip_sources').fetchone()[0])
    assert [row[0] for row in calls]==['A','outside','B']

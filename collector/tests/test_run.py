"""End-to-end cycle tests: canned XML in, database rows out. No network."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from collector import audit_db, live_db
from collector.config import Config
from collector.geo import BoundaryFilter
from collector.run import sx_cycle, vm_cycle
from fixture_gtfs import build
from test_sirisx import FEED as SX_FEED

LDN = ZoneInfo("Europe/London")
NOW = datetime(2026, 6, 10, 10, 27, 0, tzinfo=timezone.utc)  # Wed 11:27 BST

# FBRI 75 outbound, origin 11:15 BST, currently AT stop S3 (timing point,
# scheduled 11:25 BST = 10:25 UTC) at 10:27 UTC -> 120 s late.
VM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<Siri xmlns="http://www.siri.org.uk/siri" version="2.0">
 <ServiceDelivery><VehicleMonitoringDelivery>
  <VehicleActivity>
   <RecordedAtTime>2026-06-10T10:27:00+00:00</RecordedAtTime>
   <MonitoredVehicleJourney>
    <LineRef>75</LineRef>
    <DirectionRef>OUTBOUND</DirectionRef>
    <FramedVehicleJourneyRef><DatedVehicleJourneyRef>1115</DatedVehicleJourneyRef></FramedVehicleJourneyRef>
    <PublishedLineName>75</PublishedLineName>
    <OperatorRef>FBRI</OperatorRef>
    <OriginAimedDepartureTime>2026-06-10T11:15:00+01:00</OriginAimedDepartureTime>
    <DestinationName>End</DestinationName>
    <VehicleLocation><Longitude>-2.5890</Longitude><Latitude>51.4500</Latitude></VehicleLocation>
    <BlockRef>B1</BlockRef>
    <VehicleRef>FBRI-36205</VehicleRef>
   </MonitoredVehicleJourney>
  </VehicleActivity>
 </VehicleMonitoringDelivery></ServiceDelivery>
</Siri>"""


class BristolBoxBoundary(BoundaryFilter):
    """Test double: crude Bristol bounding box instead of the GeoJSON polygon
    (BoundaryFilter(None) is a let-everything-through fallback, which would
    defeat the SX scope-filter assertions)."""

    def __init__(self):
        pass

    @property
    def active(self):
        return True

    def contains(self, lat, lon):
        return 51.2 < lat < 51.7 and -3.2 < lon < -2.2


def setup():
    return (build().cursor(), live_db.connect(), audit_db.connect(),
            BristolBoxBoundary(), Config(bods_api_key="test"))


def test_vm_cycle_end_to_end():
    tt, live_conn, audit_conn, boundary, cfg = setup()
    r = vm_cycle(lambda: VM_FEED, tt, live_conn, audit_conn, boundary, cfg,
                 LDN, now_utc=NOW)
    assert r["ok"] and r["candidates"] == 1 and r["matched"] == 1
    # settled reading written to audit.db (S3 is a timing point), 120 s late,
    # inside the DfT band -> on_time
    obs = audit_conn.execute("SELECT * FROM timepoint_observations").fetchall()
    assert len(obs) == 1
    (sdate, op, route, trip, ref, seq, stop, sched, delay, on_time, dist,
     rec, veh, is_origin, match_tier) = obs[0]
    assert (op, route, trip, stop, delay, on_time) == ("FBRI", "75", "T_OUT", "0100C", 120, 1)
    assert is_origin == 0
    assert match_tier == "fuzzy"
    # vehicle row in live.db with the live estimate
    v = live_conn.execute("SELECT * FROM vehicles").fetchone()
    assert v["delay_seconds"] == 120 and v["event_type"] == "punctual"
    assert v["block_ref"] == "B1" and v["trip_id"] == "T_OUT"
    # punctual -> no event, ever
    assert r["events"] == 0
    assert r["evidence_written"] == 0
    assert audit_conn.execute(
        "SELECT COUNT(*) FROM matching_evidence").fetchone()[0] == 0


def test_vm_cycle_corroborated_event():
    tt, live_conn, audit_conn, boundary, cfg = setup()
    late = VM_FEED.replace("10:27:00+00:00", "10:32:00+00:00")  # 420 s late
    r1 = vm_cycle(lambda: late, tt, live_conn, audit_conn, boundary, cfg, LDN,
                  now_utc=NOW)
    r2 = vm_cycle(lambda: late, tt, live_conn, audit_conn, boundary, cfg, LDN,
                  now_utc=NOW)
    assert r1["events"] == 0 and r2["events"] == 1  # second agreeing poll emits
    ev = live_conn.execute("SELECT * FROM events").fetchone()
    assert ev["delay_seconds"] == 420 and ev["event_type"] == "delayed"
    assert ev["corroboration"] == 2 and ev["consumed_by_bot_at"] is None


def test_vm_cycle_failed_fetch_keeps_state():
    tt, live_conn, audit_conn, boundary, cfg = setup()
    vm_cycle(lambda: VM_FEED, tt, live_conn, audit_conn, boundary, cfg, LDN,
             now_utc=NOW)
    r = vm_cycle(lambda: None, tt, live_conn, audit_conn, boundary, cfg, LDN,
                 now_utc=NOW)
    assert not r["ok"]
    # last-known-good: vehicle row survives a failed poll
    assert live_conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 1
    st = live_conn.execute(
        "SELECT * FROM poller_status WHERE name='siri_vm'").fetchone()
    assert st["consecutive_failures"] == 1 and st["last_success_at"] is not None


def test_sx_cycle_upsert_and_close():
    _, live_conn, _, boundary, _ = setup()
    r = sx_cycle(lambda: SX_FEED, live_conn, boundary, {"FBRI"})
    assert r["ok"] and r["in_scope"] == 1
    row = live_conn.execute("SELECT * FROM situations").fetchone()
    assert row["participant"] == "WestofEngland" and row["closed_at"] is None
    # situation vanishes from the next poll -> closed, kept for history
    empty = SX_FEED.split("<PtSituationElement>")[0] + "</Situations></SituationExchangeDelivery></ServiceDelivery></Siri>"
    sx_cycle(lambda: empty, live_conn, boundary, {"FBRI"})
    row2 = live_conn.execute("SELECT * FROM situations").fetchone()
    assert row2["closed_at"] is not None


def test_stale_recorded_at_is_a_ghost_not_a_bus():
    """BODS re-broadcasts parked vehicles with old RecordedAtTime; those
    snapshots must be skipped entirely (the frozen-city-centre bug)."""
    stale = VM_FEED.replace("<RecordedAtTime>2026-06-10T10:27:00+00:00",
                            "<RecordedAtTime>2026-06-10T10:07:00+00:00")
    tt, live_conn, audit_conn, boundary, cfg = setup()
    r = vm_cycle(lambda: stale, tt, live_conn, audit_conn, boundary, cfg,
                 LDN, now_utc=NOW)
    assert r["stale"] == 1
    assert r["candidates"] == 0
    row = live_conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()
    assert row[0] == 0
    poll = audit_conn.execute(
        "SELECT stale FROM poll_log ORDER BY poll_at DESC LIMIT 1").fetchone()
    assert poll[0] == 1


def test_fresh_recorded_at_still_processed():
    tt, live_conn, audit_conn, boundary, cfg = setup()
    r = vm_cycle(lambda: VM_FEED, tt, live_conn, audit_conn, boundary, cfg,
                 LDN, now_utc=NOW)
    assert r["stale"] == 0
    assert r["candidates"] == 1


def test_normal_reading_does_not_run_expensive_diagnostics(monkeypatch):
    def should_not_run(*args, **kwargs):
        raise AssertionError("normal polls must not enumerate alternatives")

    monkeypatch.setattr("collector.run.explain_match", should_not_run)
    tt, live_conn, audit_conn, boundary, cfg = setup()
    result = vm_cycle(
        lambda: VM_FEED, tt, live_conn, audit_conn, boundary, cfg,
        LDN, now_utc=NOW)
    assert result["matched"] == 1
    assert result["evidence_written"] == 0


def test_sanity_rejection_is_counted_once_and_persisted():
    recorded = "2026-06-10T12:15:00+00:00"
    feed = VM_FEED.replace("2026-06-10T10:27:00+00:00", recorded)
    now = datetime(2026, 6, 10, 12, 15, tzinfo=timezone.utc)
    tt, live_conn, audit_conn, boundary, cfg = setup()

    result = vm_cycle(
        lambda: feed, tt, live_conn, audit_conn, boundary, cfg,
        LDN, now_utc=now)

    assert result["matched"] == 1
    assert result["dropped_insane"] == 1
    assert result["obs_written"] == 0
    assert result["evidence_written"] == 1
    receipt = audit_conn.execute(
        """SELECT reasons_json, chosen_trip_id, match_tier, delay_s,
                  candidate_count, timetable_route_id, timetable_service_id
           FROM matching_evidence""").fetchone()
    assert json.loads(receipt[0]) == ["sanity_rejected"]
    assert receipt[1:4] == ("T_OUT", "fuzzy", 6600)
    assert receipt[4] >= 1
    assert receipt[5:7] == ("R75F", "WK")
    poll = audit_conn.execute(
        """SELECT dropped_insane, stale, evidence_written, evidence_dropped
           FROM poll_log""").fetchone()
    assert tuple(poll) == (1, 0, 1, 0)


def test_extreme_but_valid_delay_leaves_evidence_receipt():
    recorded = "2026-06-10T11:10:00+00:00"  # 45 minutes after S3
    feed = VM_FEED.replace("2026-06-10T10:27:00+00:00", recorded)
    now = datetime(2026, 6, 10, 11, 10, tzinfo=timezone.utc)
    tt, live_conn, audit_conn, boundary, cfg = setup()

    result = vm_cycle(
        lambda: feed, tt, live_conn, audit_conn, boundary, cfg,
        LDN, now_utc=now)

    assert result["evidence_written"] == 1
    receipt = audit_conn.execute(
        "SELECT reasons_json, delay_s, event_type FROM matching_evidence"
    ).fetchone()
    assert json.loads(receipt[0]) == ["extreme_delay"]
    assert receipt[1:] == (2700, "delayed")


def test_match_and_direction_flip_within_one_run_leave_one_receipt():
    tt, live_conn, audit_conn, boundary, cfg = setup()
    vm_cycle(lambda: VM_FEED, tt, live_conn, audit_conn, boundary, cfg, LDN,
             now_utc=NOW)
    flipped = VM_FEED.replace(
        "<DirectionRef>OUTBOUND</DirectionRef>",
        "<DirectionRef>INBOUND</DirectionRef>").replace(
        "2026-06-10T10:27:00+00:00", "2026-06-10T10:28:00+00:00")

    result = vm_cycle(
        lambda: flipped, tt, live_conn, audit_conn, boundary, cfg, LDN,
        now_utc=datetime(2026, 6, 10, 10, 28, tzinfo=timezone.utc))

    assert result["evidence_written"] == 1
    receipt = audit_conn.execute(
        "SELECT reasons_json, chosen_trip_id FROM matching_evidence").fetchone()
    assert json.loads(receipt[0]) == [
        "direction_changed_within_run", "match_changed_within_run"]
    assert receipt[1] == "T_IN"

from datetime import datetime
from zoneinfo import ZoneInfo

import xmltodict

from collector.siri import (activities_from_xmltodict, anchor_departure_local,
                            extract_snapshot, get_nested_value)

LDN = ZoneInfo("Europe/London")

SIRI_ONE_VEHICLE = """<?xml version="1.0" encoding="UTF-8"?>
<Siri xmlns="http://www.siri.org.uk/siri" version="2.0">
 <ServiceDelivery>
  <VehicleMonitoringDelivery>
   <VehicleActivity>
    <RecordedAtTime>2026-06-09T11:30:15+00:00</RecordedAtTime>
    <MonitoredVehicleJourney>
     <LineRef>75</LineRef>
     <DirectionRef>OUTBOUND</DirectionRef>
     <FramedVehicleJourneyRef>
      <DataFrameRef>2026-06-09</DataFrameRef>
      <DatedVehicleJourneyRef>1115</DatedVehicleJourneyRef>
     </FramedVehicleJourneyRef>
     <PublishedLineName>75__</PublishedLineName>
     <OperatorRef>FBRI</OperatorRef>
     <OriginRef>0100BRP90345</OriginRef>
     <DestinationRef>0170SGB20128</DestinationRef>
     <OriginAimedDepartureTime>2026-06-09T11:15:00+01:00</OriginAimedDepartureTime>
     <DestinationName>Hengrove_Park A</DestinationName>
     <VehicleLocation><Longitude>-2.589</Longitude><Latitude>51.4536</Latitude></VehicleLocation>
     <Bearing>185.0</Bearing>
     <BlockRef>7012</BlockRef>
     <VehicleRef>FBRI-36205</VehicleRef>
    </MonitoredVehicleJourney>
   </VehicleActivity>
  </VehicleMonitoringDelivery>
 </ServiceDelivery>
</Siri>"""


def parsed():
    return xmltodict.parse(SIRI_ONE_VEHICLE, process_namespaces=False)


def test_single_activity_normalised_to_list():
    acts = activities_from_xmltodict(parsed())
    assert isinstance(acts, list) and len(acts) == 1


def test_snapshot_extraction():
    snap = extract_snapshot(activities_from_xmltodict(parsed())[0])
    assert snap is not None
    assert snap.operator_ref == "FBRI"
    assert snap.line == "75"             # trailing __ stripped
    assert snap.direction == "outbound"  # lowercased
    assert snap.vehicle_ref == "FBRI-36205"
    assert snap.bearing == 185.0
    assert snap.block_ref == "7012"
    assert snap.origin_stop_ref == "0100BRP90345"
    assert abs(snap.lat - 51.4536) < 1e-9
    assert snap.recorded_utc.tzinfo is not None


def test_anchor_prefers_origin_aimed():
    acts = activities_from_xmltodict(parsed())
    mvj = get_nested_value(acts[0], "MonitoredVehicleJourney")
    now_local = datetime(2026, 6, 9, 11, 40, tzinfo=LDN)
    anchor, src = anchor_departure_local(mvj, LDN, now_local)
    assert src == "origin"
    assert anchor.hour == 11 and anchor.minute == 15


def test_anchor_falls_back_to_hhmm_ref():
    acts = activities_from_xmltodict(parsed())
    mvj = dict(get_nested_value(acts[0], "MonitoredVehicleJourney"))
    mvj.pop("OriginAimedDepartureTime")
    now_local = datetime(2026, 6, 9, 11, 40, tzinfo=LDN)
    anchor, src = anchor_departure_local(mvj, LDN, now_local)
    assert src == "ref"
    assert (anchor.hour, anchor.minute) == (11, 15)


def test_hhmm_fallback_uses_previous_day_after_midnight():
    acts = activities_from_xmltodict(parsed())
    mvj = dict(get_nested_value(acts[0], "MonitoredVehicleJourney"))
    mvj.pop("OriginAimedDepartureTime")
    mvj["FramedVehicleJourneyRef"] = {"DatedVehicleJourneyRef": "2350"}
    now_local = datetime(2026, 6, 10, 0, 20, tzinfo=LDN)
    anchor, src = anchor_departure_local(mvj, LDN, now_local)
    assert src == "ref"
    assert anchor == datetime(2026, 6, 9, 23, 50, tzinfo=LDN)


def test_unusable_activity_returns_none():
    assert extract_snapshot({"MonitoredVehicleJourney": {"OperatorRef": "FBRI"}}) is None


def test_clean_destination():
    from collector.siri import clean_destination
    # trailing single-letter bay codes are artefacts and get stripped
    assert clean_destination("Cribbs_Causeway_Bus_Station__D_") == "Cribbs Causeway Bus Station"
    assert clean_destination("Hengrove_Park A") == "Hengrove Park"
    assert clean_destination(None) == ""

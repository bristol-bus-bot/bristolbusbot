"""Synthetic cancellation fixture based on the live BODS journey shape.

All identifiers, routes, stops and times are invented.  The fixture contains no
raw production record, API key, publisher free text or personal information.
"""
import xmltodict
import pytest
import requests

from collector.cancellations import (
    parse_cancellation_journeys,
    summarise_cancellations,
)
from collector import check_cancellations
from collector.check_cancellations import analyse_xml


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<Siri version="2.0" xmlns="http://www.siri.org.uk/siri">
 <ServiceDelivery>
  <ResponseTimestamp>2026-08-20T12:00:00Z</ResponseTimestamp>
  <SituationExchangeDelivery>
   <Situations>
    <PtSituationElement>
     <ParticipantRef>ExamplePublisher</ParticipantRef>
     <SituationNumber>synthetic-open-1</SituationNumber>
     <Version>2</Version>
     <Progress>open</Progress>
     <ValidityPeriod>
      <StartTime>2026-08-20T09:00:00Z</StartTime>
      <EndTime>2026-08-20T10:00:00Z</EndTime>
     </ValidityPeriod>
     <Affects>
      <VehicleJourneys>
       <AffectedVehicleJourney>
        <DatedVehicleJourneyRef>synthetic-duty-101</DatedVehicleJourneyRef>
        <Operator><OperatorRef>FBRI</OperatorRef></Operator>
        <LineRef>synthetic-line-1</LineRef>
        <PublishedLineName>T1</PublishedLineName>
        <OriginAimedDepartureTime>2026-08-20T09:00:00Z</OriginAimedDepartureTime>
        <JourneyCondition>cancelled</JourneyCondition>
        <Route/>
        <Calls>
         <Call><StopPointRef>0100SYNTHA</StopPointRef><CallCondition>notStopping</CallCondition></Call>
         <Call><StopPointRef>0100SYNTHB</StopPointRef><CallCondition>notStopping</CallCondition></Call>
        </Calls>
       </AffectedVehicleJourney>
      </VehicleJourneys>
     </Affects>
    </PtSituationElement>
    <PtSituationElement>
     <ParticipantRef>ExamplePublisher</ParticipantRef>
     <SituationNumber>synthetic-closed-2</SituationNumber>
     <Version>3</Version>
     <VersionedAtTime>2026-08-20T11:30:00Z</VersionedAtTime>
     <Progress>closed</Progress>
     <ValidityPeriod><StartTime>2026-08-20T11:00:00Z</StartTime></ValidityPeriod>
     <Consequences><Consequence><Condition>altered</Condition></Consequence></Consequences>
     <Affects>
      <VehicleJourneys>
       <AffectedVehicleJourney>
        <VehicleJourneyRef>synthetic-journey-202</VehicleJourneyRef>
        <DatedVehicleJourneyRef>synthetic-duty-202</DatedVehicleJourneyRef>
        <Operator><OperatorRef>SCGL</OperatorRef></Operator>
        <LineRef>synthetic-line-2</LineRef>
        <PublishedLineName>T2</PublishedLineName>
        <OriginAimedDepartureTime>2026-08-20T11:00:00Z</OriginAimedDepartureTime>
        <DestinationAimedArrivalTime>2026-08-20T12:00:00Z</DestinationAimedArrivalTime>
        <Route><RouteRef>synthetic-route</RouteRef></Route>
        <Calls>
         <Call><StopPointRef>1600SYNTHC</StopPointRef><CallCondition>stop</CallCondition></Call>
         <Call><StopPointRef>1600SYNTHD</StopPointRef><CallCondition>notStopping</CallCondition></Call>
        </Calls>
       </AffectedVehicleJourney>
      </VehicleJourneys>
     </Affects>
    </PtSituationElement>
   </Situations>
  </SituationExchangeDelivery>
 </ServiceDelivery>
</Siri>"""


def test_parse_journey_level_cancellation_fields():
    rows = parse_cancellation_journeys(xmltodict.parse(FEED))
    assert len(rows) == 2

    cancelled = rows[0]
    assert cancelled.operator_ref == "FBRI"
    assert cancelled.version == 2 and cancelled.progress == "open"
    assert cancelled.dated_vehicle_journey_ref == "synthetic-duty-101"
    assert cancelled.vehicle_journey_ref == ""
    assert cancelled.published_line_name == "T1"
    assert cancelled.origin_aimed_departure_time == "2026-08-20T09:00:00Z"
    assert cancelled.journey_condition == "cancelled"
    assert cancelled.route_detail_present is False
    assert cancelled.call_conditions == {"notStopping": 2}
    assert cancelled.stop_ref_prefixes_4 == {"0100": 2}

    altered = rows[1]
    assert altered.operator_ref == "SCGL"
    assert altered.versioned_at == "2026-08-20T11:30:00Z"
    assert altered.validity_end == ""
    assert altered.journey_condition == "altered"
    assert altered.route_detail_present is True
    assert altered.call_conditions == {"notStopping": 1, "stop": 1}
    assert altered.stop_ref_prefixes_4 == {"1600": 2}


def test_safe_summary_includes_requested_zeroes_and_no_identifiers():
    rows = parse_cancellation_journeys(xmltodict.parse(FEED))
    summary = summarise_cancellations(
        rows,
        ["FBRI", "FSAV", "SCGL", "LEMB"],
        ["0100", "0170", "0180", "0190"],
    )

    assert summary["situations"] == 2
    assert summary["publishing_operators"] == {"FBRI": 1, "SCGL": 1}
    assert summary["target_operators"]["FBRI"]["conditions"] == {
        "cancelled": 1,
    }
    assert summary["target_operators"]["SCGL"]["conditions"] == {
        "altered": 1,
    }
    assert summary["target_operators"]["LEMB"]["journeys"] == 0
    assert summary["target_operators"]["FSAV"]["journeys"] == 0
    assert summary["target_operators"]["FBRI"]["stop_ref_prefixes_4"] == {
        "0100": 2,
    }
    assert summary["target_operators"]["FBRI"]["field_presence"] == {
        "vehicle_journey_ref": 0,
        "dated_vehicle_journey_ref": 1,
        "line_ref": 1,
        "published_line_name": 1,
        "origin_aimed_departure_time": 1,
        "destination_aimed_arrival_time": 0,
        "route_detail": 0,
        "calls": 1,
    }
    assert summary["target_geography"] == {
        "requested_stop_ref_prefixes_4": ["0100", "0170", "0180", "0190"],
        "journeys_touching_requested_prefixes": 1,
        "publishing_operators": {"FBRI": 1},
        "conditions": {"cancelled": 1},
        "progress": {"open": 1},
        "matching_call_prefixes_4": {"0100": 2},
        "journeys_without_calls": 0,
    }
    assert "synthetic-open-1" not in str(summary)
    assert "synthetic-duty-101" not in str(summary)


def test_analyse_xml_adds_feed_timestamp():
    summary = analyse_xml(FEED, ["FBRI"], ["0100"])
    assert summary["response_timestamp"] == "2026-08-20T12:00:00Z"
    assert summary["target_geography"][
        "journeys_touching_requested_prefixes"] == 1


def test_fetch_failure_does_not_reveal_api_key(monkeypatch):
    def fail(*_args, **_kwargs):
        raise requests.ConnectionError(
            "failed https://example.invalid/?api_key=secret-value&thing=1")

    monkeypatch.setattr(check_cancellations.requests, "get", fail)
    with pytest.raises(RuntimeError) as error:
        check_cancellations.fetch_cancellations("secret-value")

    assert "secret-value" not in str(error.value)
    assert "api_key=[REDACTED]" in str(error.value)

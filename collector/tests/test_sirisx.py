"""Fixture XML mirrors the real feed structure captured in the live probe of
2026-06-09 (see COLLECTOR_SPEC.md §2.2): one WECA situation affecting an FBRI
line + one stop, and one WYCA situation that must be filtered out."""
import xmltodict

from collector.sirisx import in_scope, parse_situations

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<Siri version="2.0" xmlns="http://www.siri.org.uk/siri">
 <ServiceDelivery>
  <SituationExchangeDelivery>
   <Situations>
    <PtSituationElement>
     <CreationTime>2025-03-06T09:16:59Z</CreationTime>
     <ParticipantRef>WestofEngland</ParticipantRef>
     <SituationNumber>ba174836-2698-4f06-8f22-e6c90aca9a7d</SituationNumber>
     <Version>1</Version>
     <Progress>open</Progress>
     <ValidityPeriod><StartTime>2025-03-06T09:16:00.000Z</StartTime></ValidityPeriod>
     <MiscellaneousReason>roadworks</MiscellaneousReason>
     <Planned>false</Planned>
     <Summary>Tower Road/Station Road</Summary>
     <Description>Service 43 diverts via A4174 missing Baden Road stop.</Description>
     <Consequences>
      <Consequence>
       <Condition>unknown</Condition>
       <Severity>normal</Severity>
       <Affects>
        <Networks>
         <AffectedNetwork>
          <VehicleMode>bus</VehicleMode>
          <AffectedLine>
           <AffectedOperator>
            <OperatorRef>FBRI</OperatorRef>
            <OperatorName>First Bristol Limited</OperatorName>
           </AffectedOperator>
           <LineRef>43</LineRef>
           <PublishedLineName>43</PublishedLineName>
           <Direction><DirectionRef>outboundFromTown</DirectionRef></Direction>
          </AffectedLine>
         </AffectedNetwork>
        </Networks>
        <StopPoints>
         <AffectedStopPoint>
          <StopPointRef>0170SGB20128</StopPointRef>
          <StopPointName>Baden Road</StopPointName>
          <Location><Longitude>-2.47972</Longitude><Latitude>51.46012</Latitude></Location>
         </AffectedStopPoint>
        </StopPoints>
       </Affects>
       <Advice><Details>Missing Baden Road Stop only.</Details></Advice>
      </Consequence>
     </Consequences>
    </PtSituationElement>
    <PtSituationElement>
     <ParticipantRef>WYCA</ParticipantRef>
     <SituationNumber>2b9e1d8f-b0ee-43a7-8ca5-7334d6fc4587</SituationNumber>
     <Version>3</Version>
     <Progress>open</Progress>
     <Planned>true</Planned>
     <Summary>Leeds roadworks</Summary>
     <Description>Leeds thing.</Description>
     <Consequences>
      <Consequence>
       <Severity>slight</Severity>
       <Affects>
        <StopPoints>
         <AffectedStopPoint>
          <StopPointRef>450032047</StopPointRef>
          <StopPointName>Bletchley Avenue</StopPointName>
          <Location><Longitude>-1.6657</Longitude><Latitude>53.8324</Latitude></Location>
         </AffectedStopPoint>
        </StopPoints>
       </Affects>
      </Consequence>
     </Consequences>
    </PtSituationElement>
   </Situations>
  </SituationExchangeDelivery>
 </ServiceDelivery>
</Siri>"""


def bristol_only(lat, lon):
    return 51.2 < lat < 51.7 and -3.2 < lon < -2.2


def test_parse_both_situations():
    sits = parse_situations(xmltodict.parse(FEED))
    assert len(sits) == 2
    weca = sits[0]
    assert weca.situation_number.startswith("ba174836")
    assert weca.participant == "WestofEngland"
    assert weca.reason == "roadworks" and weca.planned is False
    assert weca.severity == "normal"
    assert weca.advice == "Missing Baden Road Stop only."
    assert weca.affected_lines == [{"operator": "FBRI", "line": "43",
                                    "direction": "outboundFromTown"}]
    assert weca.affected_stops[0]["stop_ref"] == "0170SGB20128"
    assert abs(weca.affected_stops[0]["lat"] - 51.46012) < 1e-9
    assert "FBRI" in weca.affected_operators


def test_scope_filter():
    sits = parse_situations(xmltodict.parse(FEED))
    kept = [s for s in sits if in_scope(s, bristol_only, {"FBRI"})]
    assert len(kept) == 1 and kept[0].participant == "WestofEngland"


def test_affected_json_roundtrip():
    import json
    sits = parse_situations(xmltodict.parse(FEED))
    blob = json.loads(sits[0].affected_json)
    assert blob["lines"][0]["line"] == "43"
    assert blob["stops"][0]["name"] == "Baden Road"

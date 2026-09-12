"""SIRI-SX (disruptions) parsing and scope filtering. Spec §2.2.

The feed is NATIONAL with no server-side filter; we keep a situation if any
of: published by WestofEngland; any affected stop inside the WECA polygon;
any affected line belongs to an operator we observe AND geography doesn't
rule it out. Everything else is dropped.

Situations are stored by participant and situation number, replaced by equal
or newer versions, and withdrawn when they vanish from a successful poll.
Withdrawal is not evidence that service has resumed. Every validity period
is preserved, including separate daily windows.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .siri import get_nested_value


def _as_list(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


@dataclass
class Situation:
    situation_number: str
    version: int
    participant: str
    progress: str
    planned: bool
    reason: str
    summary: str
    description: str
    advice: str
    severity: str
    validity_start: str | None
    validity_end: str | None
    versioned_at: str | None
    link: str | None
    affected_operators: list = field(default_factory=list)   # ["FBRI", ...] or ["ALL"]
    affected_lines: list = field(default_factory=list)       # [{operator,line,direction}]
    affected_stops: list = field(default_factory=list)       # [{stop_ref,name,lat,lon}]
    validity_periods: list = field(default_factory=list)

    @property
    def affected_json(self) -> str:
        return json.dumps({
            "operators": self.affected_operators,
            "lines": self.affected_lines,
            "stops": self.affected_stops,
            # Additive JSON field: preserves disjoint windows without a DB rebuild.
            "validity_periods": self.validity_periods,
        }, separators=(",", ":"))


def parse_situations(parsed_xml: dict) -> list[Situation]:
    """xmltodict-parsed SIRI-SX document -> list of Situation."""
    sits = get_nested_value(
        parsed_xml, "Siri/ServiceDelivery/SituationExchangeDelivery/Situations")
    out = []
    for el in _as_list((sits or {}).get("PtSituationElement")):
        if not isinstance(el, dict):
            continue
        number = str(el.get("SituationNumber") or "").strip()
        if not number:
            continue

        operators: list = []
        lines: list = []
        stops: list = []
        severity = ""
        advice_parts: list = []

        for cons in _as_list(get_nested_value(el, "Consequences/Consequence")):
            if not isinstance(cons, dict):
                continue
            severity = severity or str(cons.get("Severity") or "")
            adv = get_nested_value(cons, "Advice/Details")
            if adv:
                advice_parts.append(str(adv))
            affects = cons.get("Affects") or {}

            if get_nested_value(affects, "Operators/AllOperators") is not None \
                    or "AllOperators" in str(affects.get("Operators") or ""):
                operators.append("ALL")

            for net in _as_list(get_nested_value(affects, "Networks/AffectedNetwork")):
                if not isinstance(net, dict):
                    continue
                for line in _as_list(net.get("AffectedLine")):
                    if not isinstance(line, dict):
                        continue
                    op = str(get_nested_value(line, "AffectedOperator/OperatorRef") or "")
                    lines.append({
                        "operator": op,
                        "line": str(line.get("PublishedLineName")
                                    or line.get("LineRef") or ""),
                        "direction": str(get_nested_value(
                            line, "Direction/DirectionRef") or ""),
                    })
                    if op and op not in operators:
                        operators.append(op)

            for sp in _as_list(get_nested_value(affects, "StopPoints/AffectedStopPoint")):
                if not isinstance(sp, dict):
                    continue
                try:
                    lat = float(get_nested_value(sp, "Location/Latitude"))
                    lon = float(get_nested_value(sp, "Location/Longitude"))
                except (TypeError, ValueError):
                    lat = lon = None
                stops.append({
                    "stop_ref": str(sp.get("StopPointRef") or ""),
                    "name": str(sp.get("StopPointName") or ""),
                    "lat": lat, "lon": lon,
                })

        periods = [p for p in _as_list(el.get("ValidityPeriod")) if isinstance(p, dict)]
        single = periods[0] if len(periods) == 1 else {}
        links = _as_list(get_nested_value(el, "InfoLinks/InfoLink"))
        out.append(Situation(
            situation_number=number,
            version=int(el.get("Version") or 0),
            participant=str(el.get("ParticipantRef") or ""),
            progress=str(el.get("Progress") or ""),
            planned=str(el.get("Planned")).lower() == "true",
            reason=str(el.get("MiscellaneousReason") or ""),
            summary=str(el.get("Summary") or ""),
            description=str(el.get("Description") or ""),
            advice=" / ".join(advice_parts),
            severity=severity,
            validity_start=single.get("StartTime"),
            validity_end=single.get("EndTime"),
            versioned_at=el.get("VersionedAtTime"),
            link=next((p.get("Uri") for p in links if isinstance(p, dict) and p.get("Uri")), None),
            affected_operators=operators,
            affected_lines=lines,
            affected_stops=stops,
            validity_periods=periods,
        ))
    return out


WECA_PARTICIPANT = "WestofEngland"


def in_scope(sit: Situation, boundary_contains, observed_nocs: set[str]) -> bool:
    """Scope filter per spec §2.2. boundary_contains(lat, lon) -> bool."""
    if sit.participant == WECA_PARTICIPANT:
        return True
    for stop in sit.affected_stops:
        if stop["lat"] is not None and boundary_contains(stop["lat"], stop["lon"]):
            return True
    if not sit.affected_stops:  # no geography given: fall back to operator overlap
        for line in sit.affected_lines:
            if line["operator"] in observed_nocs:
                return True
    return False

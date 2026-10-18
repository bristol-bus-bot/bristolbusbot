#!/usr/bin/env python3
"""
The one operator registry for the audit, the collector and the timetable
build. Every operator code the system treats specially is listed here, and
every other list is derived from it:

* ``publish``: the operator appears in the public WECA audit. Every scheduled
  local bus operator serving West of England stops is published. Express
  coaches and ferries are not local bus registrations, and the WECA benchmark
  is a local bus standard, so they stay on the live map and departure boards
  but are never published in the audit.
* ``sources``: the timetable build fetches the operator's own BODS
  TransXChange files and uses them as evidence (twin proofs, quality gates).
  First's files are mandatory; every other operator's are optional, and a
  failure for them never fails the build.
* ``repairs``: the calendar and duplicate repairs may change this operator's
  trips. Only operators whose source files have been inspected are enabled.
* ``cancellations``: include in the SIRI-SX cancellation check.
* ``siri_aliases``: other ``OperatorRef`` values the live feed uses for this
  operator; the collector maps them to ``code`` on ingest.

The collector cannot import the pipeline package on the Pi, so an identical
copy lives at ``collector/src/collector/operator_registry.py``; a test fails
if the two ever differ. To add or change an operator, edit this file, copy it
there, release the pipeline and collector, and re-run the rollup.

NETWORK_LABEL is the synthetic operator code used for the combined
"whole network" figures (all published operators pooled).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Operator:
    code: str
    name: str
    kind: str = "local_bus"          # local_bus | coach | ferry
    publish: bool = True
    sources: bool = True
    repairs: bool = False
    cancellations: bool = True
    siri_aliases: tuple[str, ...] = ()
    short: str = ""                  # tag on pooled network route labels


REGISTRY: tuple[Operator, ...] = (
    Operator("FBRI", "First Bristol", repairs=True),
    Operator("SSWL", "Stagecoach South Wales", short="Stagecoach"),
    Operator("SCGL", "Stagecoach West", short="Stagecoach West"),
    Operator("FSRV", "Faresaver", short="Faresaver"),
    Operator("LEMB", "The Big Lemon", short="Big Lemon"),
    Operator("KEMT", "Kempsford Transport", short="Kempsford"),
    Operator("ABUS", "Abus", short="Abus"),
    Operator("CTCO", "CT Coaches", short="CT Coaches"),
    Operator("TYSW", "Taylors Travel", short="Taylors"),
    Operator("LTRV", "Libra Travel", short="Libra"),
    Operator("FRMN", "FromeBus", short="FromeBus"),
    Operator("NWPT", "Newport Bus", short="Newport Bus"),
    Operator("TDTR", "Swindon's Bus Company", short="Swindon's Bus Co"),
    Operator("PULH", "Pulhams Coaches", short="Pulhams"),
    Operator("COAC", "Coachstyle", short="Coachstyle"),
    Operator("EUTX", "Eurocoaches", short="Eurocoaches"),
    Operator("BDOL", "Bakers Dolphin", short="Bakers Dolphin"),
    # On the live map and boards, never in the audit.
    Operator("NATX", "National Express", kind="coach", publish=False,
             sources=False, cancellations=False),
    Operator("FLIX", "FlixBus", kind="coach", publish=False,
             sources=False, cancellations=False),
    Operator("SDVN", "Stagecoach South West (Falcon)", kind="coach",
             publish=False, sources=False, cancellations=False),
    Operator("BFBC", "Bristol Ferry Boat Company", kind="ferry",
             publish=False, sources=False, cancellations=False),
    Operator("NSEV", "Number Seven Boat Trips", kind="ferry",
             publish=False, sources=False, cancellations=False),
)

BY_CODE = {operator.code: operator for operator in REGISTRY}

# Published audit operators, First first so existing single-operator readers
# keep their default.
SHOW_OPERATORS = [operator.code for operator in REGISTRY if operator.publish]
SOURCE_OPERATORS = [operator.code for operator in REGISTRY if operator.sources]
REPAIR_OPERATORS = [operator.code for operator in REGISTRY if operator.repairs]
# FSAV is the ceased identity of First West of England's licence; SIRI-SX
# cancellations have been seen under it, so the check keeps listening for it.
CANCELLATION_OPERATORS = [
    code
    for operator in REGISTRY if operator.cancellations
    for code in (operator.code, *operator.siri_aliases)
] + ["FSAV"]
OPERATOR_NAMES = {operator.code: operator.name for operator in REGISTRY}
SIRI_ALIASES = {
    alias: operator.code
    for operator in REGISTRY for alias in operator.siri_aliases
}

NETWORK_LABEL = "ALL"
NETWORK_NAME = "WECA network"


def operator_name(code):
    if code == NETWORK_LABEL:
        return NETWORK_NAME
    return OPERATOR_NAMES.get(code, code)


def public_route(operator, route, pooled):
    """The route label used in a rollup.

    One operator's own figures use its route number as printed. The pooled
    network figures mix operators, and route numbers are reused (First's 13 in
    Bath, Stagecoach's 13 in Bristol), so every operator except First gets a
    short tag: "13 Stagecoach". First keeps bare numbers so its long history
    in the network view stays continuous.
    """
    if not pooled or route is None or operator == "FBRI":
        return route
    entry = BY_CODE.get(operator)
    tag = entry.short if entry and entry.short else operator
    return f"{route} {tag}"


def canonical_operator(code):
    """Map a live-feed OperatorRef to the timetable's operator code."""
    value = (code or "").strip()
    return SIRI_ALIASES.get(value, value)

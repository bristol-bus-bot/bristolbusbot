#!/usr/bin/env python3
"""
Shared WECA operator allowlist. This is the single place that decides which
operators the audit publishes. The collector captures every operator that
broadcasts; the snapshot, rollup and export only act on this list. To add or
drop an operator, edit SHOW_OPERATORS here and re-run the rollup.

NETWORK_LABEL is the synthetic operator code used for the combined
"whole network" figures (all show operators pooled).
"""

SHOW_OPERATORS = ["FBRI", "SCGL", "LEMB", "ABUS", "CTCO", "TYSW"]

OPERATOR_NAMES = {
    "FBRI": "First Bristol",
    "SCGL": "Stagecoach West",
    "LEMB": "The Big Lemon",
    "ABUS": "Abus",
    "CTCO": "CT Coaches",
    "TYSW": "Taylors Travel",
}

NETWORK_LABEL = "ALL"
NETWORK_NAME = "WECA network"


def operator_name(code):
    if code == NETWORK_LABEL:
        return NETWORK_NAME
    return OPERATOR_NAMES.get(code, code)

"""bbb-collector: the only process in the ecosystem that talks to BODS.

The full design rationale lives in docs/plans/COLLECTOR_SPEC.md; this
package implements that spec.

Module map:
    siri.py       - SIRI-VM XML navigation and field extraction
    timeparse.py  - GTFS times (incl. >24:00) and ISO timestamps
    geo.py        - haversine, bearing, WECA boundary point-in-polygon
    delay.py      - the two delay methods (live estimate / settled readings)
"""

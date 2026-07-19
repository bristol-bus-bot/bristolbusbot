"""Load fleet identity, livery and vehicle-description data."""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Operator-level fallback liveries, for operators absent from bustimes.org
# or whose recorded livery is plain white (invisible on the dark map).
OPERATOR_LIVERIES = {
    "NATX": {"name": "National Express",
             "left": "linear-gradient(135deg, #0C69B2 40%, #fff 40% 60%, #E7373F 60%)"},
    "LEMB": {"name": "The Big Lemon",
             "left": "linear-gradient(135deg, #FFFF00 60%, #222 60%)"},
    "EUTX": {"name": "Eurocoaches", "left": "#555"},
    "VITR": {"name": "Kempsford Transport", "left": "#555"},
}

_WHITES = ("#fff", "#FFF", "#ffffff", "#FFFFFF", "white")


class Fleet:
    def __init__(self, fleet_path: str, descriptions_path: str = "",
                 waiting_path: str = "", depot_path: str = ""):
        self._fleet = self._load(fleet_path)
        self._descriptions = self._load(descriptions_path)
        self._waiting = self._load(waiting_path)
        self._depot = self._load(depot_path)

    @staticmethod
    def _load(path: str) -> dict:
        if not path or not Path(path).exists():
            if path:
                logger.warning("fleet data missing: %s", path)
            return {}
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, list):
            # Index list data by both fleet code and registration because live
            # vehicle references can use either form.
            lookup: dict = {}
            for bus in raw:
                key = str(bus.get("fleet_code") or bus.get("fleet_number", ""))
                if key:
                    lookup[key] = bus
                reg = (bus.get("reg") or "").upper().replace(" ", "")
                if reg:
                    lookup[reg] = bus
                    lookup[reg.replace("_", "")] = bus
            return lookup
        return raw

    @property
    def raw_list(self) -> list:
        """Deduplicated vehicle records (for the /api/fleet search payload)."""
        seen, out = set(), []
        for bus in self._fleet.values():
            marker = id(bus)
            if marker not in seen:
                seen.add(marker)
                out.append(bus)
        return out

    def details(self, vehicle_ref: str, operator_ref: str = "") -> dict:
        """Return the public identity fields for a vehicle."""
        livery = model = fleet_number = reg = None
        extras: dict = {}
        if self._fleet and vehicle_ref:
            # Refs vary: 'FBRI-39465' (fleet code), 'BV24ZGL' (bare reg),
            # 'BF67_WGU' (reg w/ underscores), '354' (bare fleet number)
            fleet_number = vehicle_ref.split("-")[-1]
            bus = self._fleet.get(fleet_number)
            if not bus:
                normalised = vehicle_ref.upper().replace("_", "").replace("-", "")
                bus = self._fleet.get(normalised)
                if bus:
                    fleet_number = str(bus.get("fleet_code")
                                       or bus.get("fleet_number", ""))
            if bus:
                livery = bus.get("livery")
                reg = bus.get("reg")
                vtype = bus.get("vehicle_type") or {}
                model = vtype.get("name")
                garage = bus.get("garage") or {}
                extras = {
                    "fuel": vtype.get("fuel"),
                    "isDoubleDecker": vtype.get("double_decker", False),
                    "isElectric": vtype.get("electric", False),
                    "isCoach": vtype.get("coach", False),
                    "specialFeatures": bus.get("special_features") or [],
                    "garage": garage.get("name"),
                    "branding": bus.get("branding") or None,
                }
        if not livery or (isinstance(livery, dict)
                          and livery.get("left") in _WHITES):
            livery = OPERATOR_LIVERIES.get(operator_ref) or livery
        return {"livery": livery, "model": model, "fleetNumber": fleet_number,
                "reg": reg, "extras": extras}

    def description(self, fleet_number: str | None,
                    state: str = "in_service") -> str | None:
        """AI blurb for the bus, by state: 'in_service' | 'waiting' | 'depot'.
        The waiting/depot sets are state-specific writing (asleep-at-the-depot
        jokes etc.); each falls back to the in-service blurb."""
        if not fleet_number:
            return None
        pool = {"waiting": self._waiting, "depot": self._depot}.get(state, {})
        return pool.get(fleet_number) or self._descriptions.get(fleet_number)

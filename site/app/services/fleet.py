"""Load operator-safe fleet identity, livery and vehicle-description data."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
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
# Do not invent a specific brand when the source only says "White". A neutral
# contrast-safe marker is honest, visible on the map, and clearly labelled as
# a fallback in the API extras.
UNKNOWN_LIVERY = {
    "name": "Livery not supplied",
    "left": "#64748b",
    "right": "#475569",
}

_WHITES = {"#fff", "#ffffff", "white"}
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def _operator(record: dict) -> str:
    value = record.get("operator") or {}
    if isinstance(value, dict):
        value = value.get("id")
    return str(value or "").strip().upper()


def _code(record: dict) -> str:
    return str(record.get("fleet_code")
               or record.get("fleet_number") or "").strip()


def _registration(value) -> str:
    return _NON_ALNUM.sub("", str(value or "").strip().upper())


def _preferred(records: list[dict]) -> dict | None:
    """Choose one physical vehicle or refuse a reused ambiguous fleet code."""
    active = [record for record in records if not record.get("withdrawn")]
    candidates = active or records
    registrations = {
        _registration(record.get("reg")) for record in candidates
        if _registration(record.get("reg"))
    }
    if len(candidates) == 1 or len(registrations) == 1:
        return candidates[-1]
    return None


def _preferred_registration(records: list[dict]) -> dict | None:
    """A registration fallback must not cross operator/fleet identities."""
    active = [record for record in records if not record.get("withdrawn")]
    candidates = active or records
    identities = {(_operator(record), _code(record)) for record in candidates}
    return candidates[-1] if len(identities) == 1 else None


class Fleet:
    def __init__(self, fleet_path: str, descriptions_path: str = "",
                 waiting_path: str = "", depot_path: str = ""):
        raw, self._status = self._load_fleet(fleet_path)
        if isinstance(raw, list):
            self._records = [item for item in raw if isinstance(item, dict)]
        elif isinstance(raw, dict):
            # Retain compatibility with the old lookup-shaped fixture/file.
            self._records = []
            for key, item in raw.items():
                if isinstance(item, dict):
                    record = dict(item)
                    record.setdefault("fleet_code", str(key))
                    self._records.append(record)
        else:
            self._records = []
        self._status["records"] = len(self._records)

        self._descriptions, in_service_status = self._load_description(
            descriptions_path)
        self._waiting, waiting_status = self._load_description(waiting_path)
        self._depot, depot_status = self._load_description(depot_path)
        self._status["descriptions"] = {
            "in_service": in_service_status,
            "waiting": waiting_status,
            "depot": depot_status,
        }
        self._scoped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self._by_code: dict[str, list[dict]] = defaultdict(list)
        self._registration_scoped: dict[tuple[str, str], dict] = {}
        self._by_registration: dict[str, list[dict]] = defaultdict(list)
        self._code_owners: dict[str, set[str]] = defaultdict(set)
        for record in self._records:
            operator = _operator(record)
            code = _code(record)
            registration = _registration(record.get("reg"))
            if code:
                self._by_code[code].append(record)
                if operator:
                    self._scoped[(operator, code)].append(record)
                    if not record.get("withdrawn"):
                        self._code_owners[code].add(operator)
            if registration:
                self._by_registration[registration].append(record)
                if operator:
                    self._registration_scoped[(operator, registration)] = record
        self._ambiguous_codes = {
            code for code, records in self._by_code.items()
            if len(self._code_owners.get(code, set())) > 1
            or _preferred(records) is None
        }

    @staticmethod
    def _load(path: str):
        if not path or not Path(path).exists():
            if path:
                logger.warning("fleet data missing: %s", path)
            return {}
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("fleet data could not be loaded %s: %s", path, exc)
            return {}

    @classmethod
    def _load_dict(cls, path: str) -> dict:
        value = cls._load(path)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _load_description(path: str) -> tuple[dict, dict]:
        status = {"loaded": False, "path": path, "sha256": None,
                  "records": 0}
        try:
            raw = Path(path).read_bytes()
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("description file is not an object")
            descriptions = {
                str(key): text for key, text in value.items()
                if isinstance(text, str) and text.strip()
            }
            if len(descriptions) != len(value):
                raise ValueError("description file has invalid entries")
            status.update({
                "loaded": True,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "records": len(descriptions),
            })
            return descriptions, status
        except (OSError, json.JSONDecodeError, UnicodeDecodeError,
                ValueError) as exc:
            logger.warning("description data could not be loaded %s: %s",
                           path, exc)
            status["error"] = str(exc)[:200]
            return {}, status

    @staticmethod
    def _load_fleet(path: str) -> tuple[object, dict]:
        status = {
            "loaded": False,
            "path": path,
            "sha256": None,
            "records": 0,
        }
        try:
            raw = Path(path).read_bytes()
            value = json.loads(raw)
            status.update({
                "loaded": True,
                "sha256": hashlib.sha256(raw).hexdigest(),
            })
            return value, status
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            status["error"] = str(exc)[:200]
            return {}, status

    @property
    def status(self) -> dict:
        """Exact fleet bytes and parsed record count loaded at process start."""
        return dict(self._status)

    @property
    def raw_list(self) -> list:
        """Vehicle records for the /api/fleet search payload."""
        return list(self._records)

    @property
    def ambiguous_fleet_codes(self) -> set[str]:
        return set(self._ambiguous_codes)

    @staticmethod
    def _possible_codes(vehicle_ref: str) -> list[str]:
        raw = str(vehicle_ref or "").strip()
        values = [raw]
        if "-" in raw:
            values.append(raw.rsplit("-", 1)[-1])
        return list(dict.fromkeys(value for value in values if value))

    def _record(self, vehicle_ref: str, operator_ref: str) -> dict | None:
        operator = str(operator_ref or "").strip().upper()
        # Live references may be OPERATOR-REGISTRATION. Try the full value and
        # the suffix as registrations before falling back to fleet codes.
        for value in self._possible_codes(vehicle_ref):
            registration = _registration(value)
            if operator:
                record = self._registration_scoped.get(
                    (operator, registration))
                if record:
                    return record
            else:
                record = _preferred_registration(
                    self._by_registration.get(registration, []))
                if record:
                    return record

        for code in self._possible_codes(vehicle_ref):
            if operator and (operator, code) in self._scoped:
                return _preferred(self._scoped[(operator, code)])

        # Legacy fallback is allowed only when the code identifies one
        # unambiguous physical vehicle and does not contradict a known operator.
        for code in self._possible_codes(vehicle_ref):
            records = self._by_code.get(code, [])
            record = _preferred(records)
            owners = self._code_owners.get(code, set())
            if (record and len(owners) <= 1
                    and (not operator or not owners or operator in owners)):
                return record
        return None

    def details(self, vehicle_ref: str, operator_ref: str = "") -> dict:
        """Return public identity fields without crossing operator boundaries."""
        livery = model = fleet_number = reg = None
        extras: dict = {}
        record = self._record(vehicle_ref, operator_ref) if vehicle_ref else None
        possible = self._possible_codes(vehicle_ref)
        if possible:
            fleet_number = possible[-1]
        if record:
            fleet_number = _code(record) or fleet_number
            livery = record.get("livery")
            reg = record.get("reg")
            vtype = record.get("vehicle_type") or {}
            model = vtype.get("name")
            garage = record.get("garage") or {}
            extras = {
                "fuel": vtype.get("fuel"),
                "isDoubleDecker": vtype.get("double_decker", False),
                "isElectric": vtype.get("electric", False),
                "isCoach": vtype.get("coach", False),
                "specialFeatures": record.get("special_features") or [],
                "garage": garage.get("name"),
                "branding": record.get("branding") or None,
            }
        missing_livery = bool(record) and (
            not livery or (isinstance(livery, dict)
                           and (str(livery.get("left") or "").lower() in _WHITES
                                or str(livery.get("right") or "").lower()
                                in _WHITES)))
        if not livery or missing_livery:
            fallback = OPERATOR_LIVERIES.get(
                str(operator_ref or "").upper())
            if fallback or missing_livery:
                livery = dict(fallback or UNKNOWN_LIVERY)
                extras["liveryFallback"] = True
        return {"livery": livery, "model": model, "fleetNumber": fleet_number,
                "reg": reg, "extras": extras}

    def description(self, fleet_number: str | None,
                    state: str = "in_service",
                    operator_ref: str = "") -> str | None:
        """Return scoped flavour text, with legacy fallback only when safe."""
        if not fleet_number:
            return None
        code = str(fleet_number)
        operator = str(operator_ref or "").strip().upper()
        pool = {"waiting": self._waiting, "depot": self._depot}.get(state, {})
        scoped_key = f"{operator}:{code}" if operator else ""
        if scoped_key and scoped_key in pool:
            return pool[scoped_key]
        if scoped_key and scoped_key in self._descriptions:
            return self._descriptions[scoped_key]
        if code in self._ambiguous_codes:
            return None
        return pool.get(code) or self._descriptions.get(code)

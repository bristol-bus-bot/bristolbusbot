#!/usr/bin/env python3
"""Promote one allowlisted Pi enrichment artifact through the safe transaction."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from data_promotion import ArtifactContract, DataPromotionError, promote
from enrichment_contracts import (
    compare_fleet,
    compare_localities,
    validate_fleet,
    validate_localities,
)


ENRICHMENT_ROOT = Path("/var/lib/bristolbusbot/enrichment")
MONITORING_ROOT = Path("/var/lib/bristolbusbot/monitoring")
SITE_HEALTH = "http://127.0.0.1:5002/healthz"
SITE_LOCALITIES = "http://127.0.0.1:5002/api/stops-with-locality"
BOT_HEALTH = "http://127.0.0.1:3010/api/health"


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    filename: str
    maximum_bytes: int
    validate: Callable[[bytes], Mapping[str, object]]
    compare: Callable[
        [Mapping[str, object], Mapping[str, object]], Mapping[str, object]]


SPECS = {
    "fleet": ArtifactSpec(
        "fleet", "fbribuses.json", 32 * 1024 * 1024,
        validate_fleet, compare_fleet),
    "localities": ArtifactSpec(
        "localities", "stop_localities.json", 16 * 1024 * 1024,
        validate_localities, compare_localities),
}


def contract_for(spec: ArtifactSpec, *, root: Path = ENRICHMENT_ROOT,
                 monitoring: Path = MONITORING_ROOT) -> ArtifactContract:
    return ArtifactContract(
        name=spec.name,
        live=root / spec.filename,
        candidate=root / "incoming" / spec.filename,
        previous=root / f"{spec.filename}.previous",
        state=monitoring / f"enrichment-{spec.name}-promotion.json",
        maximum_bytes=spec.maximum_bytes,
    )


def restart_consumers() -> None:
    result = subprocess.run(
        ["systemctl", "restart", "bbb-site.service", "bbb-bot.service"],
        check=False, capture_output=True, text=True)
    if result.returncode:
        raise DataPromotionError(
            f"consumer restart failed: {result.stderr.strip()[:200]}")


def _json_url(url: str, maximum: int = 2 * 1024 * 1024) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        raw = response.read(maximum + 1)
    if len(raw) > maximum:
        raise DataPromotionError("health response exceeded its size limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise DataPromotionError("health response has the wrong shape")
    return value


def _service_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet",
         "bbb-site.service", "bbb-bot.service"],
        check=False, capture_output=True, text=True)
    return result.returncode == 0


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def health_once(spec: ArtifactSpec, live: Path, expected: str,
                summary: Mapping[str, object]) -> bool:
    try:
        if not _service_active() or _file_digest(live) != expected:
            return False
        site = _json_url(SITE_HEALTH)
        if site.get("status") not in ("ok", "warn"):
            return False
        bot = _json_url(BOT_HEALTH)
        if bot.get("success") is not True or bot.get("runtime") != "systemd":
            return False
        health_data = bot["details"]["healthData"]
        if health_data["database"]["timetable"]["connected"] is not True \
                or health_data["database"]["appData"]["connected"] is not True:
            return False
        application = health_data["application"]
        bot_status = application["enrichmentData"][spec.name]
        if bot_status.get("loaded") is not True \
                or bot_status.get("sha256") != expected \
                or bot_status.get("records") != summary.get("records"):
            return False
        site_status = site["checks"][spec.name]
        if site_status.get("loaded") is not True \
                or site_status.get("sha256") != expected \
                or site_status.get("records") != summary.get("records"):
            return False
        if spec.name == "fleet":
            return True
        stops = _json_url(SITE_LOCALITIES, maximum=16 * 1024 * 1024)
        endpoint_status = stops.get("localities")
        return isinstance(stops.get("stops"), list) and bool(stops["stops"]) \
            and isinstance(endpoint_status, dict) \
            and endpoint_status.get("loaded") is True \
            and endpoint_status.get("sha256") == expected \
            and endpoint_status.get("records") == summary.get("records")
    except (DataPromotionError, json.JSONDecodeError, KeyError, OSError,
            TypeError, ValueError):
        return False


def wait_healthy(spec: ArtifactSpec, live: Path, expected: str,
                 summary: Mapping[str, object], attempts: int = 30) -> bool:
    for _ in range(attempts):
        if health_once(spec, live, expected, summary):
            return True
        time.sleep(2)
    return False


PromotionHealth = Callable[
    [ArtifactSpec, Path, str, Mapping[str, object]], bool]


def promote_named(
    name: str,
    *,
    root: Path = ENRICHMENT_ROOT,
    monitoring: Path = MONITORING_ROOT,
    restart: Callable[[], None] = restart_consumers,
    healthy: PromotionHealth = wait_healthy,
) -> tuple[int, dict[str, object]]:
    try:
        spec = SPECS[name]
    except KeyError as exc:
        raise DataPromotionError("artifact is not allowlisted") from exc
    contract = contract_for(spec, root=root, monitoring=monitoring)
    return promote(
        contract,
        validate=spec.validate,
        compare=spec.compare,
        restart=restart,
        healthy=lambda expected, summary: healthy(
            spec, contract.live, expected, summary),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", choices=sorted(SPECS))
    args = parser.parse_args(argv)
    try:
        code, record = promote_named(args.artifact)
    except (DataPromotionError, OSError) as exc:
        parser.exit(1, f"enrichment promotion rejected: {exc}\n")
    print(json.dumps({
        "status": record["outcome"],
        "artifact": record["artifact"],
        "candidate": record["candidate"],
        "comparison": record.get("comparison"),
    }, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

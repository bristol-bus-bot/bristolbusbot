"""One-off, read-only checker for the dedicated BODS cancellation endpoint.

It prints aggregate counts only: no API key, raw XML, free text, situation ID or
journey ID is written to stdout or stored on disk.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import requests
import xmltodict
from dotenv import load_dotenv

from .cancellations import parse_cancellation_journeys, summarise_cancellations
from .secret_filter import redact_query_secrets
from .siri import get_nested_value


CANCELLATIONS_URL = (
    "https://data.bus-data.dft.gov.uk/api/v1/siri-sx/cancellations/"
)
DEFAULT_OPERATORS = [
    "FBRI",
    "FSAV",  # Ceased historical identity for First West of England's licence.
    "SCGL",
    "LEMB",
    "ABUS",
    "CTCO",
    "TYSW",
]
WECA_STOP_PREFIXES_4 = ["0100", "0170", "0180", "0190"]


def fetch_cancellations(api_key: str, timeout_s: int = 90) -> str:
    try:
        response = requests.get(
            CANCELLATIONS_URL,
            params={"api_key": api_key},
            timeout=timeout_s,
        )
        response.raise_for_status()
        return response.text
    except requests.RequestException as exc:
        safe_error = redact_query_secrets(exc)
        raise RuntimeError(f"BODS cancellation request failed: {safe_error}") from None


def analyse_xml(
        xml: str, target_operators: list[str],
        target_stop_prefixes: list[str] | None = None) -> dict:
    parsed = xmltodict.parse(xml)
    rows = parse_cancellation_journeys(parsed)
    summary = summarise_cancellations(
        rows,
        target_operators,
        target_stop_prefixes,
    )
    summary["response_timestamp"] = str(get_nested_value(
        parsed, "Siri/ServiceDelivery/ResponseTimestamp") or "")
    return summary


def _load_key() -> str:
    load_dotenv()
    if not os.getenv("BODS_API_KEY"):
        collector_env = Path(__file__).resolve().parents[2] / ".env"
        load_dotenv(collector_env)
    key = os.getenv("BODS_API_KEY", "").strip()
    if not key:
        raise SystemExit("BODS_API_KEY is not set")
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path,
        help="analyse a local SIRI-SX XML file instead of calling BODS",
    )
    parser.add_argument(
        "--operators", nargs="+", default=DEFAULT_OPERATORS,
        help="operator NOC codes to report even when their count is zero",
    )
    parser.add_argument(
        "--stop-prefixes", nargs="+", default=WECA_STOP_PREFIXES_4,
        help=("four-character ATCO stop prefixes used for a geography check "
              "independent of operator code"),
    )
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    if args.input:
        xml = args.input.read_text(encoding="utf-8")
    else:
        xml = fetch_cancellations(_load_key(), args.timeout)
    print(json.dumps(
        analyse_xml(xml, args.operators, args.stop_prefixes),
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()

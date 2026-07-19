#!/usr/bin/env python3
"""Atomically promote the materialised audit integration after Pages publish."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from audit_integration import write_atomic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if payload.get("schema") != 1:
        raise RuntimeError("refusing audit integration with unsupported schema")
    payload["published_at"] = datetime.now(timezone.utc).isoformat()
    write_atomic(args.output, payload)
    print(f"Promoted {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

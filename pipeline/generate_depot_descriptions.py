#!/usr/bin/env python3
"""Compatibility entrypoint for the fail-closed blurb workflow."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    workflow = Path(__file__).resolve().parents[1] / "deploy" / "blurb_automation.py"
    if not workflow.is_file():
        print("The legacy direct generator is retired; use bbb-blurb-review on the Pi.",
              file=sys.stderr)
        return 1
    return subprocess.run([sys.executable, str(workflow), "generate"],
                          check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())

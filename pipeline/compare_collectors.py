#!/usr/bin/env python3
"""Compatibility entry point for pre-2026 staleness systemd units.

New installations call ``check_collector_freshness.py`` directly. Keep this
small forwarding entry point in collector releases so an older installed unit
continues to work while its unit files are upgraded.
"""
from check_collector_freshness import main


if __name__ == "__main__":
    raise SystemExit(main())

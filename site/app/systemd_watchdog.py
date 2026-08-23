"""Dependency-free systemd watchdog notification for the site supervisor."""
from __future__ import annotations

import logging
import os
import socket
from collections.abc import Mapping


logger = logging.getLogger(__name__)


def notify_watchdog(environment: Mapping[str, str] | None = None) -> bool:
    environment = os.environ if environment is None else environment
    address = environment.get("NOTIFY_SOCKET")
    if not address or not environment.get("WATCHDOG_USEC"):
        return False
    watchdog_pid = environment.get("WATCHDOG_PID")
    if watchdog_pid:
        try:
            if int(watchdog_pid) != os.getpid():
                return False
        except ValueError:
            return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.settimeout(1.0)
            notifier.connect(address)
            notifier.sendall(b"WATCHDOG=1")
        return True
    except OSError as exc:
        logger.warning("could not notify systemd watchdog: %s", exc)
        return False

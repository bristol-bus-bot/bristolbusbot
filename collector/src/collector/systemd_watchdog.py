"""Small, dependency-free systemd watchdog notifier.

The caller decides what counts as useful progress. This module only delivers
that decision to systemd when the service has a watchdog configured.
"""
from __future__ import annotations

import logging
import os
import socket
from collections.abc import Mapping
from typing import Callable


logger = logging.getLogger(__name__)
SocketFactory = Callable[[int, int], socket.socket]
# Some Windows Python builds omit AF_UNIX entirely. Notifications are disabled
# there, but keeping the Linux numeric value makes the injected unit tests
# portable without changing production behaviour.
AF_UNIX = getattr(socket, "AF_UNIX", 1)


def watchdog_enabled(
    environment: Mapping[str, str] | None = None,
    *,
    pid: int | None = None,
) -> bool:
    """Return whether systemd expects watchdog messages from this process."""
    environment = os.environ if environment is None else environment
    if not environment.get("NOTIFY_SOCKET") or not environment.get("WATCHDOG_USEC"):
        return False
    watchdog_pid = environment.get("WATCHDOG_PID")
    if not watchdog_pid:
        return True
    try:
        return int(watchdog_pid) == (os.getpid() if pid is None else pid)
    except ValueError:
        return False


def notify_watchdog(
    environment: Mapping[str, str] | None = None,
    *,
    socket_factory: SocketFactory = socket.socket,
    pid: int | None = None,
) -> bool:
    """Send ``WATCHDOG=1`` without turning a notify fault into an app crash."""
    environment = os.environ if environment is None else environment
    if not watchdog_enabled(environment, pid=pid):
        return False

    address = environment["NOTIFY_SOCKET"]
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket_factory(AF_UNIX, socket.SOCK_DGRAM) as notifier:
            notifier.settimeout(1.0)
            notifier.connect(address)
            notifier.sendall(b"WATCHDOG=1")
        return True
    except OSError as exc:
        logger.warning("could not notify systemd watchdog: %s", exc)
        return False

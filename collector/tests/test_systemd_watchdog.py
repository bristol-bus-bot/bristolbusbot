from collector.systemd_watchdog import notify_watchdog, watchdog_enabled


class FakeSocket:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.address = None
        self.payload = None
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        if self.fail:
            raise OSError("test socket failure")
        self.address = address

    def sendall(self, payload):
        self.payload = payload


def test_watchdog_is_disabled_without_systemd_environment():
    assert not watchdog_enabled({})
    assert not notify_watchdog({}, socket_factory=lambda *_: FakeSocket())


def test_watchdog_rejects_another_or_invalid_pid():
    base = {"NOTIFY_SOCKET": "/run/test.sock", "WATCHDOG_USEC": "120000000"}
    assert not watchdog_enabled({**base, "WATCHDOG_PID": "42"}, pid=43)
    assert not watchdog_enabled({**base, "WATCHDOG_PID": "not-a-pid"}, pid=43)


def test_watchdog_sends_progress_to_filesystem_socket():
    made = []

    def factory(*_):
        made.append(FakeSocket())
        return made[0]

    environment = {
        "NOTIFY_SOCKET": "/run/systemd/notify",
        "WATCHDOG_USEC": "120000000",
        "WATCHDOG_PID": "123",
    }
    assert notify_watchdog(environment, socket_factory=factory, pid=123)
    assert made[0].address == "/run/systemd/notify"
    assert made[0].payload == b"WATCHDOG=1"
    assert made[0].timeout == 1.0


def test_watchdog_converts_abstract_socket_and_contains_failures():
    abstract = FakeSocket()
    environment = {"NOTIFY_SOCKET": "@test", "WATCHDOG_USEC": "120000000"}
    assert notify_watchdog(environment, socket_factory=lambda *_: abstract)
    assert abstract.address == "\0test"
    assert not notify_watchdog(
        environment, socket_factory=lambda *_: FakeSocket(fail=True))

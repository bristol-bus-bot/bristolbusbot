from app.watchdog_runner import supervise, watchdog_interval


class FakeProcess:
    def __init__(self, returncode=0):
        self.returncode = None
        self.final_returncode = returncode
        self.waits = []

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waits.append(timeout)
        self.returncode = self.final_returncode
        return self.returncode


def test_successful_real_probe_reports_progress():
    reports = []
    child = FakeProcess()
    result = supervise(
        ["gunicorn", "wsgi:app"], "http://127.0.0.1/livez", interval=30,
        probe_site=lambda _: True,
        report_progress=lambda: reports.append("progress") or True,
        process_factory=lambda _: child,
    )
    assert result == 0
    assert reports == ["progress"]
    assert child.waits == [30]


def test_failed_probe_does_not_falsely_report_progress():
    reports = []
    result = supervise(
        ["gunicorn", "wsgi:app"], "http://127.0.0.1/livez", interval=30,
        probe_site=lambda _: False,
        report_progress=lambda: reports.append("progress") or True,
        process_factory=lambda _: FakeProcess(returncode=3),
    )
    assert result == 3
    assert reports == []


def test_interval_is_safely_inside_systemd_timeout():
    assert watchdog_interval({"WATCHDOG_USEC": "120000000"}) == 30
    assert watchdog_interval({"WATCHDOG_USEC": "6000000"}) == 2
    assert watchdog_interval({"WATCHDOG_USEC": "invalid"}) == 30

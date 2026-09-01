import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import configure_carto_key as carto


VALID_KEY = "carto-project-key_1234567890"


def test_extracts_key_only_from_carto_basemap_https_url():
    assert carto.parse_key_from_url(
        "https://a.basemaps.cartocdn.com/rastertiles/voyager/1/2/3.png"
        f"?key={VALID_KEY}"
    ) == VALID_KEY


@pytest.mark.parametrize("url", [
    f"http://a.basemaps.cartocdn.com/tile.png?key={VALID_KEY}",
    f"https://evil.example/tile.png?key={VALID_KEY}",
    f"https://basemaps.cartocdn.com/tile.png?key={VALID_KEY}&key=another-key-123456",
    "https://basemaps.cartocdn.com/tile.png",
    "not a URL",
])
def test_rejects_unsafe_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        carto.parse_key_from_url(url)


def test_replace_key_removes_duplicates_without_touching_other_settings():
    original = (
        "BBB_LIVE_DB=/var/lib/bristolbusbot/collector/live.db\n"
        "BBB_CARTO_BASEMAP_KEY=old\n"
        "BBB_ENFORCE_HTTPS=true\n"
        "BBB_CARTO_BASEMAP_KEY=stale\n"
    )
    assert carto.replace_key(original, VALID_KEY) == (
        "BBB_LIVE_DB=/var/lib/bristolbusbot/collector/live.db\n"
        f"BBB_CARTO_BASEMAP_KEY={VALID_KEY}\n"
        "BBB_ENFORCE_HTTPS=true\n"
    )


def test_replace_key_adds_missing_setting():
    assert carto.replace_key("BBB_ENFORCE_HTTPS=true\n", VALID_KEY) == (
        "BBB_ENFORCE_HTTPS=true\n"
        f"BBB_CARTO_BASEMAP_KEY={VALID_KEY}\n"
    )


class FakeChannel:
    def __init__(self, status):
        self.status = status

    def recv_exit_status(self):
        return self.status


class FakeStream:
    def __init__(self, status):
        self.channel = FakeChannel(status)


class FakeSSH:
    def __init__(self, status):
        self.status = status
        self.commands = []

    def exec_command(self, command):
        self.commands.append(command)
        stream = FakeStream(self.status)
        return stream, stream, stream


def test_promote_uses_only_the_fixed_allowlisted_command():
    ssh = FakeSSH(0)
    carto.promote_candidate(ssh)
    assert ssh.commands == [carto.CONTROL_COMMAND]


def test_promote_failure_message_cannot_include_key_or_remote_output():
    ssh = FakeSSH(1)
    with pytest.raises(RuntimeError) as caught:
        carto.promote_candidate(ssh)
    assert VALID_KEY not in str(caught.value)
    assert "previous configuration retained" in str(caught.value)

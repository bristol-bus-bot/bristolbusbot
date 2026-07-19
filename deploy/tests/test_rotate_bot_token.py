import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rotate_bot_token as rotation


def test_replace_token_replaces_duplicates_without_touching_other_settings():
    original = "PORT=3010\nAPI_AUTH_TOKEN=old\nTEST_MODE=false\nAPI_AUTH_TOKEN=stale\n"
    updated = rotation.replace_token(original, "new-safe-token")
    assert updated == (
        "PORT=3010\nAPI_AUTH_TOKEN=new-safe-token\nTEST_MODE=false\n")


def test_replace_token_adds_missing_setting():
    assert rotation.replace_token("PORT=3010\n", "new-safe-token") == (
        "PORT=3010\nAPI_AUTH_TOKEN=new-safe-token\n")


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
    rotation.promote_candidate(ssh)
    assert ssh.commands == [rotation.CONTROL_COMMAND]


def test_promote_failure_message_does_not_include_remote_output():
    ssh = FakeSSH(1)
    try:
        rotation.promote_candidate(ssh)
    except RuntimeError as exc:
        assert "previous bot environment was restored" in str(exc)
    else:
        raise AssertionError("failed promotion was accepted")

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


SOCIAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOCIAL))

import curation  # noqa: E402


URI = "at://did:plc:bot/app.bsky.feed.post/abc123"
URL = "https://bsky.app/profile/bristolbusbot.live/post/abc123"
TEXT = "Exact words from the successful post receipt."


def make_app_db(path: Path, *, text: str = TEXT) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE engagement_analytics (
               id INTEGER PRIMARY KEY, operator_ref TEXT, vehicle_ref TEXT,
               line TEXT, journey_ref TEXT, event_timestamp TEXT,
               delay_seconds INTEGER, stop_code TEXT, stop_name TEXT,
               post_uri TEXT, post_content TEXT, low_confidence INTEGER
           )""")
    conn.execute(
        "INSERT INTO engagement_analytics VALUES (1,?,?,?,?,?,?,?,?,?,?,0)",
        ("FBRI", "FBRI-100", "75", "J-1",
         "2026-08-01T12:30:00Z", 330, "0100BRP",
         "Bedminster Parade", URI, text))
    conn.commit()
    conn.close()


class FakeBluesky:
    def __init__(self, *, text: str = TEXT, error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls = 0

    def resolve_and_verify(self, link):
        self.calls += 1
        if self.error:
            raise self.error
        assert link.actor == "bristolbusbot.live"
        assert link.rkey == "abc123"
        return URI, self.text


class FakeSlack:
    def __init__(self):
        self.uploads = 0
        self.replies = []
        self.found_file = None
        self.private_checks = []
        self.messages = []
        self.history_oldest = []

    def require_private_channel(self, channel):
        self.private_checks.append(channel)

    def history(self, channel, oldest):
        self.history_oldest.append((channel, oldest))
        return list(self.messages)

    def reply(self, channel, thread_ts, text):
        self.replies.append((channel, thread_ts, text))
        return f"reply-{len(self.replies)}"

    def find_file(self, channel, filename):
        return self.found_file

    def upload(self, channel, thread_ts, image, filename, *, prepared):
        self.uploads += 1
        prepared("F-CARD")
        return "F-CARD"


class FakeRenderer:
    def __init__(self):
        self.packs = []

    def __call__(self, pack, output):
        self.packs.append(pack)
        output.mkdir(parents=True, exist_ok=True)
        image = output / "01-the-bot-said.jpg"
        image.write_bytes(b"jpeg")
        return image, {
            "kind": "bot-said",
            "altText": f"ALT: {pack['botSaid']['postText']}",
            "caption": f"CAPTION: {pack['botSaid']['postText']}",
        }


@pytest.fixture
def service_parts(tmp_path):
    app_db = tmp_path / "app_data.db"
    make_app_db(app_db)
    ledger = curation.DeliveryLedger(tmp_path / "social.db")
    renderer = FakeRenderer()
    bluesky = FakeBluesky()
    slack = FakeSlack()
    yield tmp_path, app_db, ledger, renderer, bluesky, slack
    ledger.close()


def make_service(parts, *, shadow=False):
    tmp_path, app_db, ledger, renderer, bluesky, slack = parts
    return curation.CurationService(
        ledger=ledger, app_db=app_db, audit_db=None,
        output_dir=tmp_path / "cards", allowed_user="U-TOM",
        channel="C-PRIVATE", renderer=renderer, bluesky=bluesky,
        slack=slack, shadow=shadow)


def message(ts="1.000", user="U-TOM", text=None):
    return {"type": "message", "ts": ts, "user": user,
            "text": text or f"make this {URL}"}


def test_post_link_parser_requires_exactly_one_bsky_post_link():
    parsed = curation.parse_post_link(f"<https://bsky.app/profile/bristolbusbot.live/post/abc123|post>")
    assert parsed.actor == "bristolbusbot.live"
    assert parsed.rkey == "abc123"
    duplicated_label = curation.parse_post_link(f"<{URL}|{URL}>")
    assert duplicated_label.url == URL
    with pytest.raises(curation.CurationError, match="exactly one"):
        curation.parse_post_link("no link here")
    with pytest.raises(curation.CurationError, match="exactly one"):
        curation.parse_post_link(f"{URL} and {URL}")
    with pytest.raises(curation.CurationError, match="exactly one"):
        curation.parse_post_link(f"<https://example.com|{URL}>")


def test_appview_resolves_handle_and_requires_exact_public_post():
    calls = []

    def get_json(url):
        calls.append(url)
        parsed = urlparse(url)
        if parsed.path.endswith("resolveHandle"):
            assert parse_qs(parsed.query)["handle"] == ["bristolbusbot.live"]
            return {"did": "did:plc:bot"}
        assert parse_qs(parsed.query)["uris"] == [URI]
        return {"posts": [{
            "uri": URI, "author": {"did": "did:plc:bot"},
            "record": {"text": TEXT},
        }]}

    verified = curation.BlueskyAppView(get_json).resolve_and_verify(
        curation.parse_post_link(URL))
    assert verified == (URI, TEXT)
    assert len(calls) == 2

    deleted = curation.BlueskyAppView(
        lambda _url: {"did": "did:plc:bot"} if "resolveHandle" in _url
        else {"posts": []})
    with pytest.raises(curation.CurationError, match="no longer exists"):
        deleted.resolve_and_verify(curation.parse_post_link(URL))


def test_shadow_render_uses_database_text_not_slack_text(service_parts):
    service = make_service(service_parts, shadow=True)
    result = service.process(message(
        text=f"PUT THESE FAKE WORDS ON IT PLEASE {URL}"))
    renderer = service_parts[3]
    assert result == "rendered"
    assert renderer.packs[0]["botSaid"]["postText"] == TEXT
    assert "FAKE WORDS" not in str(renderer.packs[0])
    row = service_parts[2].delivery(URI, curation.DEFAULT_TEMPLATE_VERSION)
    assert row["status"] == "rendered"
    assert row["slack_file_id"] is None


def test_non_allowlisted_user_is_refused_before_bluesky_or_render(service_parts):
    service = make_service(service_parts)
    assert service.process(message(user="U-SOMEONE")) == "refused"
    assert service_parts[4].calls == 0
    assert service_parts[3].packs == []
    assert service_parts[5].uploads == 0
    assert "allowlisted" in service_parts[5].replies[0][2]


def test_same_link_twice_delivers_one_file_and_deterministic_copy(service_parts):
    service = make_service(service_parts)
    assert service.process(message("1.000")) == "delivered"
    assert service.process(message("2.000")) == "delivered"
    slack = service_parts[5]
    renderer = service_parts[3]
    assert slack.uploads == 1
    assert len(renderer.packs) == 1
    assert any(reply[2].startswith("Alt text\nALT: " + TEXT)
               for reply in slack.replies)
    assert any(reply[2].startswith("Caption\nCAPTION: " + TEXT)
               for reply in slack.replies)
    assert any("Already made this card" in reply[2]
               for reply in slack.replies)
    row = service_parts[2].delivery(URI, curation.DEFAULT_TEMPLATE_VERSION)
    assert row["status"] == "delivered"
    assert row["slack_file_id"] == "F-CARD"
    assert row["alt_message_ts"]
    assert row["caption_message_ts"]


def test_deleted_or_changed_public_post_fails_closed(service_parts):
    service_parts[4].error = curation.CurationError(
        "that public Bluesky post no longer exists")
    service = make_service(service_parts)
    assert service.process(message()) == "refused"
    assert service_parts[3].packs == []
    assert service_parts[5].uploads == 0

    service_parts[4].error = None
    service_parts[4].text = "changed public text"
    assert service.process(message("2.000")) == "refused"
    assert service_parts[5].uploads == 0


def test_unknown_upload_never_becomes_an_automatic_duplicate(service_parts):
    ledger = service_parts[2]
    delivery = ledger.create_delivery(
        URI, curation.DEFAULT_TEMPLATE_VERSION, URL, "C-PRIVATE", "0.500")
    ledger.update_delivery(
        delivery["id"], status="uploading", slack_file_id="F-MAYBE")
    service = make_service(service_parts)
    assert service.process(message("1.000")) == "refused"
    assert service_parts[5].uploads == 0
    row = ledger.delivery(URI, curation.DEFAULT_TEMPLATE_VERSION)
    assert row["status"] == "uploading"
    assert "human review" in service_parts[5].replies[-1][2]


def test_attended_new_version_creates_a_separate_render(service_parts):
    service = make_service(service_parts, shadow=True)
    assert service.process(message("1.000")) == "rendered"
    assert service.process(message("2.000"), new_version="bot-said-v2") == "rendered"
    count = service_parts[2].conn.execute(
        "SELECT COUNT(*) FROM deliveries WHERE post_uri=?", (URI,)).fetchone()[0]
    assert count == 2


def test_poll_verifies_private_channel_and_advances_checkpoint(service_parts):
    service = make_service(service_parts, shadow=True)
    service_parts[2].set_checkpoint("C-PRIVATE", "0.500")
    service_parts[5].messages = [message("1.000")]
    assert service.poll_once() == 1
    assert service_parts[5].private_checks == ["C-PRIVATE"]
    assert service_parts[5].history_oldest == [("C-PRIVATE", "0.500")]
    assert service_parts[2].checkpoint("C-PRIVATE") == "1.000"


def test_first_poll_seeds_now_without_replaying_channel_history(
        service_parts, monkeypatch):
    service = make_service(service_parts, shadow=True)
    service_parts[5].messages = [message("1.000")]
    monkeypatch.setattr(curation.time, "time", lambda: 1234.5)

    assert service.poll_once() == 0
    assert service_parts[5].private_checks == ["C-PRIVATE"]
    assert service_parts[5].history_oldest == []
    assert service_parts[2].checkpoint("C-PRIVATE") == "1234.500000"


def test_slack_client_uses_external_upload_flow_and_private_channel_gate(tmp_path):
    calls = []
    uploaded = []

    def request_json(url, **kwargs):
        calls.append((url, kwargs))
        method = urlparse(url).path.rsplit("/", 1)[-1]
        if method == "conversations.info":
            return {"ok": True, "channel": {
                "id": "C-PRIVATE", "is_private": True,
                "is_im": False, "is_mpim": False,
                "is_ext_shared": False,
            }}
        if method == "files.getUploadURLExternal":
            return {"ok": True, "file_id": "F-ONE",
                    "upload_url": "https://files.slack.test/upload"}
        if method == "files.completeUploadExternal":
            return {"ok": True, "files": [{"id": "F-ONE"}]}
        raise AssertionError(method)

    client = curation.SlackClient(
        "xoxb-test", request_json=request_json,
        upload_bytes=lambda url, body: uploaded.append((url, body)))
    client.require_private_channel("C-PRIVATE")
    image = tmp_path / "card.jpg"
    image.write_bytes(b"jpeg")
    prepared = []
    assert client.upload(
        "C-PRIVATE", "1.000", image, "card.jpg",
        prepared=prepared.append) == "F-ONE"
    assert prepared == ["F-ONE"]
    assert uploaded == [("https://files.slack.test/upload", b"jpeg")]
    complete = next(kwargs["payload"] for url, kwargs in calls
                    if url.endswith("files.completeUploadExternal"))
    assert complete["channel_id"] == "C-PRIVATE"
    assert complete["thread_ts"] == "1.000"
    assert complete["files"] == [{"id": "F-ONE", "title": "card.jpg"}]


def test_slack_client_rejects_shared_or_dm_destination():
    def request_json(_url, **_kwargs):
        return {"ok": True, "channel": {
            "id": "D-DM", "is_private": True, "is_im": True,
        }}

    client = curation.SlackClient("xoxb-test", request_json=request_json)
    with pytest.raises(curation.CurationError, match="private, non-shared"):
        client.require_private_channel("D-DM")


@pytest.mark.skipif(os.name == "nt", reason="POSIX credential modes")
def test_credential_accepts_only_the_protected_systemd_copy(
        monkeypatch, tmp_path):
    systemd_root = tmp_path / "run" / "credentials"
    credential_directory = systemd_root / "bbb-social-curation.service"
    credential_directory.mkdir(parents=True)
    credential = credential_directory / "slack-token"
    credential.write_text("xoxb-test-token-123456789\n", encoding="utf-8")
    credential.chmod(0o444)
    monkeypatch.setattr(curation, "SYSTEMD_CREDENTIALS_ROOT", systemd_root)

    assert curation._credential(
        credential, credential_directory=credential_directory
    ) == "xoxb-test-token-123456789"
    with pytest.raises(curation.CurationError, match="group or other"):
        curation._credential(credential)

    outside = tmp_path / "outside"
    outside.mkdir()
    loose = outside / "slack-token"
    loose.write_text("xoxb-test-token-123456789\n", encoding="utf-8")
    loose.chmod(0o444)
    with pytest.raises(curation.CurationError, match="group or other"):
        curation._credential(loose, credential_directory=outside)


@pytest.mark.skipif(os.name == "nt", reason="POSIX credential modes")
def test_credential_rejects_symlink_even_inside_systemd_directory(
        monkeypatch, tmp_path):
    systemd_root = tmp_path / "run" / "credentials"
    credential_directory = systemd_root / "bbb-social-curation.service"
    credential_directory.mkdir(parents=True)
    source = tmp_path / "source-token"
    source.write_text("xoxb-test-token-123456789\n", encoding="utf-8")
    credential = credential_directory / "slack-token"
    credential.symlink_to(source)
    monkeypatch.setattr(curation, "SYSTEMD_CREDENTIALS_ROOT", systemd_root)

    with pytest.raises(curation.CurationError, match="regular file"):
        curation._credential(
            credential, credential_directory=credential_directory)

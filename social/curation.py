#!/usr/bin/env python3
"""Turn an allowlisted Slack Bluesky link into one provenance-backed card.

Slack is only a request transport. Card text and facts always come from the
bot's successful-post database, and Instagram publishing remains manual.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import build_pack


APPVIEW = "https://public.api.bsky.app/xrpc"
SLACK_API = "https://slack.com/api"
SYSTEMD_CREDENTIALS_ROOT = Path("/run/credentials")
DEFAULT_TEMPLATE_VERSION = "bot-said-v1"
SAFE_VERSION = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
LINK_RE = re.compile(
    r"https://bsky\.app/profile/([^/\s?#]+)/post/([A-Za-z0-9._~:-]+)",
    re.IGNORECASE,
)
SLACK_LINK_RE = re.compile(r"<([^<>\s|]+)\|[^<>]*>")
ROUNDUP_RE = re.compile(r"^\s*roundup\s*$", re.IGNORECASE)


class CurationError(RuntimeError):
    """A request that must fail closed without rendering or uploading."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PostLink:
    actor: str
    rkey: str
    url: str


def parse_post_link(text: str) -> PostLink:
    # Slack may represent one pasted URL as <target|the same target>. Parse the
    # actual link target once and ignore its display label, which is not a
    # second user-supplied destination.
    normalized = SLACK_LINK_RE.sub(
        lambda match: match.group(1), str(text or ""))
    matches = list(LINK_RE.finditer(normalized))
    if len(matches) != 1:
        raise CurationError("share exactly one bsky.app post link")
    actor = urllib.parse.unquote(matches[0].group(1)).strip()
    rkey = matches[0].group(2).strip()
    if not actor or not rkey or rkey in {".", ".."}:
        raise CurationError("the Bluesky post link is malformed")
    return PostLink(
        actor=actor,
        rkey=rkey,
        url=f"https://bsky.app/profile/{actor}/post/{rkey}",
    )


def is_roundup_command(text: str) -> bool:
    return bool(ROUNDUP_RE.fullmatch(str(text or "")))


def roundup_command_from_message(message: dict) -> str | None:
    direct = str(message.get("text") or "")
    if is_roundup_command(direct):
        return direct

    fragments: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("type") in {"text", "mrkdwn"}:
            text = value.get("text")
            if isinstance(text, str):
                fragments.append(text)
        collect(value.get("elements"))

    collect(message.get("blocks"))
    return next((fragment for fragment in fragments
                 if is_roundup_command(fragment)), None)


def _form_body(payload: dict) -> bytes:
    values = {}
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            values[key] = json.dumps(value, separators=(",", ":"))
        else:
            values[key] = str(value)
    return urllib.parse.urlencode(values).encode("utf-8")


def _http_json(url: str, *, method: str = "GET",
               payload: dict | None = None,
               form_payload: dict | None = None,
               headers: dict[str, str] | None = None,
               timeout: float = 15) -> dict:
    if payload is not None and form_payload is not None:
        raise ValueError("choose JSON or form payload, not both")
    body = None
    request_headers = {"User-Agent": "bristolbusbot-social-curation/1"}
    request_headers.update(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json; charset=utf-8"
    elif form_payload is not None:
        body = _form_body(form_payload)
        request_headers["Content-Type"] = (
            "application/x-www-form-urlencoded; charset=utf-8")
    request = urllib.request.Request(
        url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CurationError("the remote service could not give a trustworthy answer") from exc


class BlueskyAppView:
    def __init__(self, get_json: Callable[[str], dict] | None = None):
        self.get_json = get_json or _http_json

    def _get(self, method: str, params: list[tuple[str, str]]) -> dict:
        return self.get_json(
            f"{APPVIEW}/{method}?{urllib.parse.urlencode(params)}")

    def resolve_and_verify(self, link: PostLink) -> tuple[str, str]:
        if link.actor.startswith("did:"):
            did = link.actor
        else:
            resolved = self._get(
                "com.atproto.identity.resolveHandle", [("handle", link.actor)])
            did = str(resolved.get("did") or "")
        if not did.startswith("did:"):
            raise CurationError("the Bluesky account could not be resolved")
        uri = f"at://{did}/app.bsky.feed.post/{link.rkey}"
        response = self._get("app.bsky.feed.getPosts", [("uris", uri)])
        posts = response.get("posts")
        if not isinstance(posts, list):
            raise CurationError("Bluesky did not return a valid post list")
        post = next((item for item in posts
                     if isinstance(item, dict) and item.get("uri") == uri), None)
        if post is None:
            raise CurationError("that public Bluesky post no longer exists")
        author_did = str((post.get("author") or {}).get("did") or "")
        public_text = str((post.get("record") or {}).get("text") or "")
        if author_did != did or not public_text:
            raise CurationError("Bluesky returned mismatched post identity")
        return uri, public_text


class DeliveryLedger:
    """Durable delivery attempts; this is not an Instagram posting log."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(
            """CREATE TABLE IF NOT EXISTS deliveries (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   post_uri TEXT NOT NULL,
                   card_kind TEXT NOT NULL,
                   template_version TEXT NOT NULL,
                   source_url TEXT NOT NULL,
                   status TEXT NOT NULL,
                   filename TEXT NOT NULL,
                   rendered_path TEXT,
                   slack_channel_id TEXT,
                   first_request_ts TEXT,
                   slack_file_id TEXT,
                   alt_message_ts TEXT,
                   caption_message_ts TEXT,
                   last_error TEXT,
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   UNIQUE(post_uri, card_kind, template_version)
               );
               CREATE TABLE IF NOT EXISTS requests (
                   channel_id TEXT NOT NULL,
                   message_ts TEXT NOT NULL,
                   user_id TEXT NOT NULL,
                   source_url TEXT,
                   delivery_id INTEGER,
                   outcome TEXT NOT NULL,
                   error TEXT,
                   updated_at TEXT NOT NULL,
                   PRIMARY KEY(channel_id, message_ts),
                   FOREIGN KEY(delivery_id) REFERENCES deliveries(id)
               );
               CREATE TABLE IF NOT EXISTS state (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL
               );""")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def request(self, channel: str, ts: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM requests WHERE channel_id=? AND message_ts=?",
            (channel, ts)).fetchone()

    def save_request(self, channel: str, ts: str, user: str, *,
                     outcome: str, source_url: str | None = None,
                     delivery_id: int | None = None,
                     error: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO requests
                   (channel_id,message_ts,user_id,source_url,delivery_id,
                    outcome,error,updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(channel_id,message_ts) DO UPDATE SET
                   source_url=excluded.source_url,
                   delivery_id=excluded.delivery_id,
                   outcome=excluded.outcome,
                   error=excluded.error,
                   updated_at=excluded.updated_at""",
            (channel, ts, user, source_url, delivery_id, outcome, error,
             utc_now()))
        self.conn.commit()

    def delivery(self, uri: str, version: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT * FROM deliveries
                WHERE post_uri=? AND card_kind='bot-said'
                  AND template_version=?""", (uri, version)).fetchone()

    def create_delivery(self, uri: str, version: str, source_url: str,
                        channel: str, request_ts: str) -> sqlite3.Row:
        digest = hashlib.sha256(f"{uri}\0{version}".encode()).hexdigest()[:16]
        filename = f"bbb-bot-said-{digest}.jpg"
        now = utc_now()
        self.conn.execute(
            """INSERT OR IGNORE INTO deliveries
                   (post_uri,card_kind,template_version,source_url,status,
                    filename,slack_channel_id,first_request_ts,created_at,
                    updated_at)
               VALUES (?,'bot-said',?,?, 'received', ?,?,?,?,?)""",
            (uri, version, source_url, filename, channel, request_ts, now, now))
        self.conn.commit()
        row = self.delivery(uri, version)
        assert row is not None
        return row

    def update_delivery(self, delivery_id: int, **fields: Any) -> sqlite3.Row:
        allowed = {
            "status", "rendered_path", "slack_file_id", "alt_message_ts",
            "caption_message_ts", "last_error",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unknown delivery fields: {sorted(unknown)}")
        values = {**fields, "updated_at": utc_now()}
        assignments = ", ".join(f"{key}=?" for key in values)
        self.conn.execute(
            f"UPDATE deliveries SET {assignments} WHERE id=?",
            (*values.values(), delivery_id))
        self.conn.commit()
        return self.conn.execute(
            "SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()

    def checkpoint(self, channel: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM state WHERE key=?", (f"checkpoint:{channel}",)
        ).fetchone()
        return str(row[0]) if row else None

    def set_checkpoint(self, channel: str, ts: str) -> None:
        self.conn.execute(
            """INSERT INTO state(key,value) VALUES (?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (f"checkpoint:{channel}", ts))
        self.conn.commit()


class SlackClient:
    def __init__(self, token: str,
                 request_json: Callable[..., dict] | None = None,
                 upload_bytes: Callable[[str, bytes], None] | None = None):
        if not token.startswith(("xoxb-", "xoxp-")):
            raise CurationError("the Slack credential is not a bot or user token")
        self.token = token
        self.request_json = request_json or _http_json
        self.upload_bytes = upload_bytes or self._upload_bytes

    @staticmethod
    def _upload_bytes(upload_url: str, contents: bytes) -> None:
        request = urllib.request.Request(
            upload_url, data=contents, method="POST",
            headers={"Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if not 200 <= response.status < 300:
                    raise CurationError("Slack rejected the image bytes")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CurationError("Slack image upload outcome is unknown") from exc

    def _api(self, method: str, payload: dict | None = None,
             *, http_method: str = "POST", form_encoded: bool = False) -> dict:
        payload = payload or {}
        if http_method == "GET":
            url = f"{SLACK_API}/{method}?{urllib.parse.urlencode(payload)}"
            response = self.request_json(
                url, headers={"Authorization": f"Bearer {self.token}"})
        elif form_encoded:
            response = self.request_json(
                f"{SLACK_API}/{method}", method="POST", form_payload=payload,
                headers={"Authorization": f"Bearer {self.token}"})
        else:
            response = self.request_json(
                f"{SLACK_API}/{method}", method="POST", payload=payload,
                headers={"Authorization": f"Bearer {self.token}"})
        if response.get("ok") is not True:
            raise CurationError(
                f"Slack {method} failed: {response.get('error') or 'unknown error'}")
        return response

    def history(self, channel: str, oldest: str) -> list[dict]:
        messages = []
        cursor = ""
        while True:
            payload = {"channel": channel, "oldest": oldest, "limit": 15}
            if cursor:
                payload["cursor"] = cursor
            response = self._api(
                "conversations.history", payload, http_method="GET")
            page = response.get("messages")
            if not isinstance(page, list):
                raise CurationError("Slack returned an invalid message list")
            messages.extend(page)
            cursor = str(
                (response.get("response_metadata") or {}).get(
                    "next_cursor") or "")
            if not cursor:
                break
        return sorted(
            (item for item in messages if isinstance(item, dict)),
            key=lambda item: str(item.get("ts") or ""))

    def require_private_channel(self, channel: str) -> None:
        response = self._api(
            "conversations.info", {"channel": channel}, http_method="GET")
        conversation = response.get("channel") or {}
        if (conversation.get("id") != channel
                or conversation.get("is_private") is not True
                or conversation.get("is_im") is True
                or conversation.get("is_mpim") is True
                or conversation.get("is_ext_shared") is True):
            raise CurationError(
                "the allowlisted Slack destination must be one private, "
                "non-shared channel")

    def reply(self, channel: str, thread_ts: str, text: str) -> str:
        response = self._api("chat.postMessage", {
            "channel": channel, "thread_ts": thread_ts, "text": text,
        })
        return str(response.get("ts") or "")

    def find_file(self, channel: str, filename: str) -> str | None:
        response = self._api("files.list", {
            "channel": channel, "count": 100, "page": 1,
        }, http_method="GET")
        for item in response.get("files") or []:
            if isinstance(item, dict) and item.get("name") == filename:
                return str(item.get("id") or "") or None
        return None

    def wait_for_thread_files(self, channel: str, thread_ts: str,
                              filenames: list[str], *,
                              timeout: float = 15) -> None:
        expected = set(filenames)
        deadline = time.monotonic() + timeout
        while True:
            response = self._api("conversations.replies", {
                "channel": channel, "ts": thread_ts, "limit": 100,
            }, http_method="GET")
            visible = {
                str(file.get("name") or "")
                for message in response.get("messages") or []
                if isinstance(message, dict)
                for file in message.get("files") or []
                if isinstance(file, dict)
            }
            if expected <= visible:
                return
            if time.monotonic() >= deadline:
                missing = ", ".join(sorted(expected - visible))
                raise CurationError(
                    "Slack did not confirm all weekly slides before the "
                    f"caption: {missing}")
            time.sleep(.5)

    def upload(self, channel: str, thread_ts: str, image: Path,
               filename: str, *, prepared: Callable[[str], None],
               alt_text: str | None = None) -> str:
        size = image.stat().st_size
        ticket_payload = {
            "filename": filename, "length": size,
        }
        if alt_text:
            ticket_payload["alt_txt"] = alt_text
        ticket = self._api(
            "files.getUploadURLExternal", ticket_payload, form_encoded=True)
        file_id = str(ticket.get("file_id") or "")
        upload_url = str(ticket.get("upload_url") or "")
        if not file_id or not upload_url:
            raise CurationError("Slack returned an incomplete upload ticket")
        prepared(file_id)
        self.upload_bytes(upload_url, image.read_bytes())
        self._api("files.completeUploadExternal", {
            "files": [{"id": file_id, "title": filename}],
            "channel_id": channel, "thread_ts": thread_ts,
        }, form_encoded=True)
        return file_id


def render_single(pack: dict, output: Path, *, node: str = "node") -> tuple[Path, dict]:
    output.mkdir(parents=True, exist_ok=True)
    pack_path = output / "pack.json"
    pack_path.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    script = Path(__file__).with_name("generate-pack.mjs")
    subprocess.run(
        [node, str(script), "--input", str(pack_path), "--output",
         str(output), "--card", "bot-said"],
        check=True, capture_output=True, text=True,
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    draft = manifest["drafts"][0]
    image = output / draft["file"]
    if not image.is_file() or draft.get("kind") != "bot-said":
        raise CurationError("the renderer did not produce one Bot Said card")
    return image, draft


def render_weekly(pack: dict, output: Path, *,
                  node: str = "node") -> tuple[list[Path], dict]:
    output.mkdir(parents=True, exist_ok=True)
    pack_path = output / "pack.json"
    pack_path.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    script = Path(__file__).with_name("generate-pack.mjs")
    subprocess.run(
        [node, str(script), "--input", str(pack_path), "--output",
         str(output), "--card", "weekly"],
        check=True, capture_output=True, text=True,
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    drafts = manifest.get("drafts") or []
    if len(drafts) != 1 or drafts[0].get("kind") != "weekly-carousel":
        raise CurationError("the renderer did not produce one weekly carousel")
    draft = drafts[0]
    slides = draft.get("slides") or []
    images = [output / str(slide.get("file") or "") for slide in slides]
    if len(images) != 6 or any(not image.is_file() for image in images):
        raise CurationError("the renderer did not produce all six weekly slides")
    return images, draft


def build_weekly_pack(audit_json: Path, audit_db: Path) -> dict:
    if not audit_json.is_file():
        raise CurationError("the published weekly audit data is unavailable")
    if not audit_db.is_file():
        raise CurationError("the audit database is unavailable")
    audit = json.loads(audit_json.read_text(encoding="utf-8"))
    conn = sqlite3.connect(
        f"{audit_db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        week = build_pack.build_week(audit)
        week["distribution"] = build_pack.build_distribution(conn, audit, week)
    finally:
        conn.close()
    return {"generatedAt": utc_now(), "busWeek": week}


class CurationService:
    def __init__(self, *, ledger: DeliveryLedger, app_db: Path,
                 audit_db: Path | None, audit_json: Path | None = None,
                 output_dir: Path,
                 allowed_user: str, channel: str,
                 bluesky: BlueskyAppView | None = None,
                 slack: SlackClient | None = None,
                 renderer: Callable[[dict, Path], tuple[Path, dict]] | None = None,
                 weekly_renderer: Callable[
                     [dict, Path], tuple[list[Path], dict]] | None = None,
                 weekly_builder: Callable[[Path, Path], dict] | None = None,
                 template_version: str = DEFAULT_TEMPLATE_VERSION,
                 shadow: bool = False):
        self.ledger = ledger
        self.app_db = app_db
        self.audit_db = audit_db
        self.audit_json = audit_json
        self.output_dir = output_dir
        self.allowed_user = allowed_user
        self.channel = channel
        self.bluesky = bluesky or BlueskyAppView()
        self.slack = slack
        self.renderer = renderer or render_single
        self.weekly_renderer = weekly_renderer or render_weekly
        self.weekly_builder = weekly_builder or build_weekly_pack
        if not SAFE_VERSION.fullmatch(template_version):
            raise ValueError("template version must be a short lowercase identifier")
        self.template_version = template_version
        self.shadow = shadow

    def _reply(self, ts: str, text: str) -> str:
        if self.shadow or self.slack is None:
            return ""
        return self.slack.reply(self.channel, ts, text)

    def _process_roundup(self, ts: str, user: str) -> str:
        if self.audit_json is None or self.audit_db is None:
            raise CurationError("weekly roundup data is not configured")
        try:
            pack = self.weekly_builder(self.audit_json, self.audit_db)
            week = pack["busWeek"]
            digest = hashlib.sha256(ts.encode("utf-8")).hexdigest()[:8]
            job_dir = self.output_dir / (
                f"roundup-{week['startDate']}-to-{week['endDate']}-{digest}")
            images, draft = self.weekly_renderer(pack, job_dir)
            if self.shadow:
                self.ledger.save_request(
                    self.channel, ts, user, outcome="rendered")
                return "rendered"
            if self.slack is None:
                raise CurationError("live delivery requires a Slack client")
            slides = draft.get("slides") or []
            if len(images) != 6 or len(slides) != 6:
                raise CurationError("the weekly roundup does not contain six slides")
            operator_slug = re.sub(
                r"[^a-z0-9]+", "-", str(week["operatorName"]).lower()
            ).strip("-") or "bus"
            role_slugs = {
                "headline": "headline",
                "target": "weca-target",
                "daily-detail": "daily-results",
                "distribution": "early-late-distribution",
                "powertrain": "electric-vs-diesel",
                "operator-comparison": "operators-compared",
            }
            filenames = []
            for index, (image, slide) in enumerate(
                    zip(images, slides), start=1):
                role = str(slide.get("role") or "")
                if role not in role_slugs or not slide.get("altText"):
                    raise CurationError(
                        "the weekly roundup has incomplete slide metadata")
                filename = (
                    f"{operator_slug}-weekly-{week['startDate']}-to-"
                    f"{week['endDate']}-slide-{index}-"
                    f"{role_slugs[role]}.jpg")
                filenames.append(filename)
                self.slack.upload(
                    self.channel, ts, image, filename,
                    prepared=lambda _file_id: None,
                    alt_text=str(slide["altText"]),
                )
            self.slack.wait_for_thread_files(
                self.channel, ts, filenames)
            self._reply(ts, f"Caption\n{draft['caption']}")
            self.ledger.save_request(
                self.channel, ts, user, outcome="delivered")
            return "delivered"
        except CurationError:
            raise
        except Exception as exc:
            raise CurationError(
                f"the weekly roundup stopped safely: {exc}") from exc

    def process(self, message: dict, *, new_version: str | None = None) -> str:
        ts = str(message.get("ts") or "")
        user = str(message.get("user") or "")
        text = str(message.get("text") or "")
        if not ts:
            raise CurationError("Slack message has no timestamp")
        prior = self.ledger.request(self.channel, ts)
        if prior is not None and prior["outcome"] in {"delivered", "rendered", "refused"}:
            return str(prior["outcome"])
        if user != self.allowed_user:
            error = "Only the allowlisted maintainer can request a card."
            self.ledger.save_request(
                self.channel, ts, user, outcome="refused", error=error)
            self._reply(ts, error)
            return "refused"
        try:
            if is_roundup_command(text):
                return self._process_roundup(ts, user)
            link = parse_post_link(text)
            uri, public_text = self.bluesky.resolve_and_verify(link)
            version = new_version or self.template_version
            if not SAFE_VERSION.fullmatch(version):
                raise CurationError(
                    "the new template version must be a short lowercase identifier")
            existing = self.ledger.delivery(uri, version)
            if existing is not None and existing["status"] == "delivered":
                self.ledger.save_request(
                    self.channel, ts, user, outcome="delivered",
                    source_url=link.url, delivery_id=existing["id"])
                self._reply(
                    ts, "Already made this card. "
                    f"Slack file {existing['slack_file_id']} was delivered from "
                    f"the original request {existing['first_request_ts']}.")
                return "delivered"
            delivery = existing or self.ledger.create_delivery(
                uri, version, link.url, self.channel, ts)
            audit_conn = None
            if self.audit_db:
                audit_conn = sqlite3.connect(
                    f"{self.audit_db.resolve().as_uri()}?mode=ro", uri=True)
            try:
                bot_said = build_pack.read_bot_post(
                    self.app_db, uri, link.url, audit_conn=audit_conn)
            finally:
                if audit_conn is not None:
                    audit_conn.close()
            if bot_said["postText"] != public_text:
                raise CurationError(
                    "the public post text no longer matches the bot's stored receipt")
            pack = {"generatedAt": utc_now(), "botSaid": bot_said}
            job_dir = self.output_dir / str(delivery["id"])
            image, draft = self.renderer(pack, job_dir)
            delivery_fields = {
                "rendered_path": str(image.resolve()), "last_error": None,
            }
            if delivery["status"] not in {
                    "unknown", "uploading", "image-delivered"}:
                delivery_fields["status"] = "rendered"
            delivery = self.ledger.update_delivery(
                delivery["id"], **delivery_fields)
            if self.shadow:
                self.ledger.save_request(
                    self.channel, ts, user, outcome="rendered",
                    source_url=link.url, delivery_id=delivery["id"])
                return "rendered"
            if self.slack is None:
                raise CurationError("live delivery requires a Slack client")
            if delivery["status"] in {"unknown", "uploading"}:
                found = self.slack.find_file(self.channel, delivery["filename"])
                if not found:
                    raise CurationError(
                        "an earlier upload has an unknown outcome; human review is required")
                delivery = self.ledger.update_delivery(
                    delivery["id"], status="image-delivered",
                    slack_file_id=found, last_error=None)
            if not delivery["slack_file_id"]:
                upload_prepared = False

                def prepared(file_id: str) -> None:
                    nonlocal upload_prepared
                    upload_prepared = True
                    self.ledger.update_delivery(
                        delivery["id"], status="uploading",
                        slack_file_id=file_id)
                try:
                    file_id = self.slack.upload(
                        self.channel, ts, image, delivery["filename"],
                        prepared=prepared)
                except Exception as exc:
                    self.ledger.update_delivery(
                        delivery["id"],
                        status="unknown" if upload_prepared else "rendered",
                        last_error=str(exc))
                    raise
                delivery = self.ledger.update_delivery(
                    delivery["id"], status="image-delivered",
                    slack_file_id=file_id, last_error=None)
            if not delivery["alt_message_ts"]:
                alt_ts = self._reply(ts, f"Alt text\n{draft['altText']}")
                delivery = self.ledger.update_delivery(
                    delivery["id"], alt_message_ts=alt_ts)
            if not delivery["caption_message_ts"]:
                caption_ts = self._reply(ts, f"Caption\n{draft['caption']}")
                delivery = self.ledger.update_delivery(
                    delivery["id"], caption_message_ts=caption_ts)
            delivery = self.ledger.update_delivery(
                delivery["id"], status="delivered", last_error=None)
            self.ledger.save_request(
                self.channel, ts, user, outcome="delivered",
                source_url=link.url, delivery_id=delivery["id"])
            return "delivered"
        except CurationError as exc:
            self.ledger.save_request(
                self.channel, ts, user, outcome="refused", error=str(exc))
            kind = "roundup" if is_roundup_command(text) else "card"
            self._reply(ts, f"Couldn't make that {kind}: {exc}")
            return "refused"
        except Exception as exc:
            self.ledger.save_request(
                self.channel, ts, user, outcome="error", error=str(exc))
            raise

    def poll_once(self) -> int:
        if self.slack is None:
            raise CurationError("polling requires a Slack client")
        self.slack.require_private_channel(self.channel)
        checkpoint = self.ledger.checkpoint(self.channel)
        if checkpoint is None:
            # First contact is deliberately a no-op. Seed at "now" so a new
            # installation cannot replay the channel's retained history.
            self.ledger.set_checkpoint(self.channel, f"{time.time():.6f}")
            return 0
        handled = 0
        for message in self.slack.history(
                self.channel, checkpoint):
            message_text = str(message.get("text") or "")
            # Slack integrations can attach non-message transport metadata to
            # a command sent on the maintainer's behalf. Exact `roundup` is
            # still authenticated inside process(), so route that single word
            # before filtering ordinary subtypes and uploaded drafts.
            roundup_text = roundup_command_from_message(message)
            if roundup_text is not None:
                self.process({**message, "text": roundup_text})
                self.ledger.set_checkpoint(self.channel, str(message["ts"]))
                handled += 1
                continue
            if message.get("type") != "message" or message.get("subtype"):
                self.ledger.set_checkpoint(self.channel, str(message.get("ts") or "0"))
                continue
            # The same private channel also holds completed Instagram drafts.
            # File uploads and ordinary conversation are not card requests.
            # Multiple Bluesky links still enter process() and fail closed
            # with the existing explanatory reply.
            if (not LINK_RE.search(message_text)
                    and not is_roundup_command(message_text)):
                self.ledger.set_checkpoint(
                    self.channel, str(message.get("ts") or "0"))
                continue
            self.process(message)
            self.ledger.set_checkpoint(self.channel, str(message["ts"]))
            handled += 1
        return handled


def _credential(path: Path, *, credential_directory: Path | None = None) -> str:
    try:
        info = path.lstat()
    except OSError:
        raise CurationError(f"Slack credential not found: {path}")
    if not stat.S_ISREG(info.st_mode):
        raise CurationError(f"Slack credential is not a regular file: {path}")
    trusted_systemd_copy = False
    if credential_directory is not None and path.name == "slack-token":
        try:
            root = SYSTEMD_CREDENTIALS_ROOT.resolve()
            directory = credential_directory.resolve(strict=True)
            source = path.resolve(strict=True)
            trusted_systemd_copy = (
                source.parent == directory
                and directory.is_relative_to(root)
            )
        except OSError:
            trusted_systemd_copy = False
    if (os.name != "nt" and info.st_mode & 0o077
            and not trusted_systemd_copy):
        raise CurationError(
            "Slack credential must not be readable by group or other users")
    token = path.read_text(encoding="utf-8").strip()
    if "\n" in token:
        raise CurationError("Slack credential must contain exactly one token")
    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True,
                        help="social.db delivery ledger")
    parser.add_argument("--app-db", type=Path, required=True)
    parser.add_argument("--audit-db", type=Path)
    parser.add_argument("--audit-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--allowed-user-id", required=True)
    parser.add_argument("--slack-credential", type=Path)
    parser.add_argument("--shadow", action="store_true")
    parser.add_argument("--link", help="attended one-link render instead of polling")
    parser.add_argument(
        "--new-version",
        help="attended regeneration under a new explicit template version")
    args = parser.parse_args(argv)
    if args.new_version and not args.link:
        parser.error("--new-version is valid only with attended --link mode")
    if args.new_version == DEFAULT_TEMPLATE_VERSION:
        parser.error("--new-version must differ from the normal template version")
    if not args.link and not args.slack_credential:
        parser.error("polling requires --slack-credential")
    if not args.link and not args.audit_json:
        parser.error("polling requires --audit-json for the roundup command")
    if not args.app_db.is_file():
        parser.error(f"bot database not found: {args.app_db}")
    if args.audit_db and not args.audit_db.is_file():
        parser.error(f"audit database not found: {args.audit_db}")
    if args.audit_json and not args.audit_json.is_file():
        parser.error(f"published audit data not found: {args.audit_json}")
    credential_directory = os.getenv("CREDENTIALS_DIRECTORY")
    slack = SlackClient(_credential(
        args.slack_credential,
        credential_directory=Path(credential_directory)
        if credential_directory else None,
    )) \
        if args.slack_credential else None
    ledger = DeliveryLedger(args.db)
    try:
        service = CurationService(
            ledger=ledger, app_db=args.app_db, audit_db=args.audit_db,
            audit_json=args.audit_json,
            output_dir=args.output_dir, allowed_user=args.allowed_user_id,
            channel=args.channel_id, slack=slack,
            shadow=args.shadow or bool(args.link))
        if args.link:
            result = service.process({
                "type": "message", "user": args.allowed_user_id,
                "ts": f"attended-{time.time_ns()}", "text": args.link,
            }, new_version=args.new_version)
            print(f"Attended card request: {result}; no Slack upload was attempted.")
        else:
            print(f"Handled {service.poll_once()} Slack request(s).")
    finally:
        ledger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fetch the approved editorial context from the repository default branch."""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from editorial_context import EditorialValidationError, validate_bytes


REPOSITORY = "bristol-bus-bot/bristolbusbot"
DEFAULT_BRANCH = "main"
REPOSITORY_PATH = "bot/data/editorial-context.json"
API_URL = (
    f"https://api.github.com/repos/{REPOSITORY}/contents/"
    f"{REPOSITORY_PATH}?ref={DEFAULT_BRANCH}"
)
EDITORIAL_ROOT = Path("/var/lib/bristolbusbot-editorial")
SHA_RE = re.compile(r"[0-9a-f]{40}")
MAX_API_BYTES = 512 * 1024


class EditorialFetchError(RuntimeError):
    """The repository response could not become a safe candidate."""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_bytes(path: Path, raw: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    temporary = path.with_name(f".{path.name}.new-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def fetch_json(opener=None) -> dict:
    opener = opener or urllib.request.build_opener()
    request = urllib.request.Request(
        API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "bristolbusbot-editorial-fetch/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    for attempt in range(1, 4):
        try:
            with opener.open(request, timeout=30) as response:
                if response.geturl().split("?", 1)[0] != API_URL.split("?", 1)[0]:
                    raise EditorialFetchError(
                        "GitHub redirected the contents API unexpectedly")
                raw = response.read(MAX_API_BYTES + 1)
                if not raw or len(raw) > MAX_API_BYTES:
                    raise EditorialFetchError(
                        "GitHub contents response has an unsafe size")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise EditorialFetchError(
                        "GitHub contents response is not an object")
                return value
        except EditorialFetchError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            raise EditorialFetchError(
                f"GitHub contents API returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError,
                json.JSONDecodeError) as exc:
            if attempt < 3:
                time.sleep(2 ** (attempt - 1))
                continue
            raise EditorialFetchError(
                "GitHub contents API could not be read after three attempts") from exc
    raise AssertionError("unreachable")


def decode_contents(value: dict) -> tuple[bytes, str]:
    if value.get("type") != "file" or value.get("path") != REPOSITORY_PATH \
            or value.get("name") != Path(REPOSITORY_PATH).name:
        raise EditorialFetchError("GitHub returned an unexpected repository path")
    blob_sha = str(value.get("sha", ""))
    if not SHA_RE.fullmatch(blob_sha):
        raise EditorialFetchError("GitHub returned an invalid blob identity")
    if value.get("encoding") != "base64" or not isinstance(value.get("content"), str):
        raise EditorialFetchError("GitHub did not return inline base64 content")
    try:
        raw = base64.b64decode(value["content"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise EditorialFetchError("GitHub returned invalid base64 content") from exc
    size = value.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size != len(raw):
        raise EditorialFetchError("GitHub content size does not match its metadata")
    return raw, blob_sha


def stage(root: Path, value: dict) -> dict:
    if root.is_symlink():
        raise EditorialFetchError("editorial root cannot be a symlink")
    raw, blob_sha = decode_contents(value)
    try:
        _, summary = validate_bytes(raw)
    except EditorialValidationError as exc:
        raise EditorialFetchError(f"editorial validation failed: {exc}") from exc
    candidate = root / "incoming" / "editorial-context.json"
    metadata = root / "incoming" / "metadata.json"
    atomic_bytes(candidate, raw)
    record = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "branch": DEFAULT_BRANCH,
        "path": REPOSITORY_PATH,
        "blob_sha": blob_sha,
        "fetched_at": utcnow(),
        "content": summary,
    }
    atomic_bytes(
        metadata,
        (json.dumps(record, indent=2, sort_keys=True) + "\n").encode(),
    )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=EDITORIAL_ROOT)
    args = parser.parse_args(argv)
    record = stage(args.root, fetch_json())
    print(json.dumps({
        "status": "staged",
        "blob_sha": record["blob_sha"],
        **record["content"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

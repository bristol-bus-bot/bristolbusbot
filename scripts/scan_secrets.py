#!/usr/bin/env python3
"""Fail if anything resembling a credential exists in tracked files.

Run before every commit of imported or unreviewed files. CI also runs this
scanner and gitleaks against repository history.

Usage:  python scripts/scan_secrets.py [path ...]

With no paths, the scanner checks tracked files and non-ignored untracked
files. This includes newly added files before they are staged.
Exit 1 on any finding. Findings print file:line with the value MASKED.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("google-api-key", re.compile(r"AIza[A-Za-z0-9_-]{30,40}")),
    ("slack-webhook", re.compile(r"hooks\.slack\.com/services/\S+")),
    ("openai/generic sk- key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("bluesky/atproto app password", re.compile(r"\b[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}\b")),
    ("hex token assigned to key-ish name", re.compile(
        r"(?i)(api_?key|token|secret|password)\s*[:=]\s*['\"][a-f0-9]{24,}['\"]")),
    ("hardcoded password assignment", re.compile(
        r"(?i)password\s*[:=]\s*['\"](?!\s*['\"])(?!.*(env|getenv|process\.))[^'\"]{6,}['\"]")),
    ("private key block", re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("dotenv-style live key line", re.compile(
        r"(?im)^(?!#)(\w*API_KEY|\w*SECRET|\w*PASSWORD)=(?!your-|<|\s*$|\$\{).{8,}$")),
]

SKIP_SUFFIXES = {".png", ".jpg", ".woff2", ".db", ".zip", ".pyc", ".ico", ".gif"}
SKIP_NAMES = {"scan_secrets.py", "package-lock.json"}
ALLOW_MARKER = "scan-secrets: allow"  # put on a line to accept a false positive


def candidate_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True,
    )
    return [Path(p) for p in out.stdout.splitlines()]


def mask(value: str) -> str:
    return value[:4] + "…" + value[-2:] if len(value) > 8 else "«masked»"


def main() -> int:
    paths = [Path(a) for a in sys.argv[1:]] or candidate_files()
    findings = 0
    for path in paths:
        if path.suffix.lower() in SKIP_SUFFIXES or path.name in SKIP_NAMES or not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:
                continue
            for label, rx in PATTERNS:
                m = rx.search(line)
                if m:
                    findings += 1
                    print(f"SECRET? {path}:{lineno} [{label}] {mask(m.group(0))}")
    if findings:
        print(f"\n{findings} finding(s). Fix them or annotate false positives with '{ALLOW_MARKER}'.")
        return 1
    print("scan_secrets: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

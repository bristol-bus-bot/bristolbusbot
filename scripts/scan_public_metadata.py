#!/usr/bin/env python3
"""Fail when tracked files contain workstation or production identity.

The local run reads ignored ``deploy/local.env`` and treats each configured
value as private. CI additionally enforces generic rules for absolute user-home
paths and local-network SSH targets, even though that ignored file is absent.
Only filenames and line numbers are printed; private values are never echoed.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCAL_ENV = ROOT / "deploy/local.env"
EXAMPLE = Path("deploy/local.env.example")
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".db",
    ".zip", ".gz", ".pyc",
}
GENERIC_PATTERNS = (
    ("literal Linux user home", re.compile(r"(?<![A-Za-z0-9_])/home/[A-Za-z0-9._-]+")),
    ("literal Windows user home", re.compile(r"[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9._-]+")),
    ("literal local-network SSH target", re.compile(
        r"\b[a-z_][a-z0-9_-]*@[A-Za-z0-9.-]+\.local\b", re.IGNORECASE)),
)
PUBLIC_CONTENT_PATTERNS = (
    ("assistant-session reference", re.compile(
        r"\b(?:Claude Code|ChatGPT|Codex)\b", re.IGNORECASE)),
    ("private planning-artifact reference", re.compile(
        r"\b(?:System 2\.0|UNIFIED_EXECUTION_PLAN|BUS_PROJECTS_REVIEW|"
        r"original-site-execution-plan)\b", re.IGNORECASE)),
    ("stale internal project wording", re.compile(
        r"binding implementation specification|locked decisions|"
        r"pre-execution review|root LICENSE file is pending|do not break it|"
        r"reserved for Step 9|refuses today|\b(?:pre|post)-cutover\b|"
        r"\bpre-launch\b|\btombstone\b", re.IGNORECASE)),
    ("hard-coded workstation checkout", re.compile(
        r"[A-Za-z]:[\\/]dev[\\/]bristolbusbot", re.IGNORECASE)),
)
DISALLOWED_ARTIFACT_NAMES = {
    "claude.md", "codex.md", "chatgpt.md", "pasted-text.txt",
    "public_readme.md",
}


def candidate_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


def private_values() -> list[tuple[str, str]]:
    if not LOCAL_ENV.is_file():
        return []
    values: list[tuple[str, str]] = []
    for raw in LOCAL_ENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'\"")
        if len(value) >= 5:
            values.append((key.strip(), value))
    return values


def main() -> int:
    findings: list[tuple[Path, int, str]] = []
    local = private_values()
    for relative in candidate_files():
        path = ROOT / relative
        if not path.is_file():
            continue
        if (relative.name.lower() in DISALLOWED_ARTIFACT_NAMES
                or ".claude" in {part.lower() for part in relative.parts}):
            findings.append((relative, 1, "internal or staging artifact"))
        if (relative == Path("scripts/scan_public_metadata.py")
                or path.suffix.lower() in SKIP_SUFFIXES):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, 1):
            lowered = line.lower()
            for key, value in local:
                if value.lower() in lowered:
                    findings.append((relative, lineno, f"private local setting {key}"))
            if relative != EXAMPLE:
                for label, pattern in GENERIC_PATTERNS:
                    if pattern.search(line):
                        findings.append((relative, lineno, label))
            for label, pattern in PUBLIC_CONTENT_PATTERNS:
                if pattern.search(line):
                    findings.append((relative, lineno, label))

    if findings:
        for path, lineno, label in findings:
            print(f"PRIVATE? {path}:{lineno} [{label}; value hidden]")
        print(f"\n{len(findings)} public-metadata finding(s).")
        return 1
    print("scan_public_metadata: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail CI/local checks if credentials or account PII would be published.

Usage:
    python3 check_secrets.py

Scans the files git would publish (or every file when not in a repo) for
real email addresses, credential assignments, JWT/GitHub tokens, private
keys, absolute home-directory paths and unredacted account context.
Only the Python standard library is used. Exit code 1 means DO NOT PUSH.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

PATTERNS: list[tuple[str, str]] = [
    ("real email address", r"\b[A-Za-z0-9._%+-]+@(?!example\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ("credential assignment", r"SUKANDA_(?:PASSWORD|TOKEN|REFRESH_TOKEN)\s*=\s*(?!<|$|your-|#)\S+"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\b"),
    ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ("private key", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("bearer token", r"Bearer\s+[A-Za-z0-9._-]{24,}"),
    ("absolute home path", r"/(?:Users|home)/[A-Za-z0-9._-]+/"),
]

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules"}
# .env and mcp-config.local.json are generated locally and are gitignored.
# check_secrets.py itself contains the detection patterns, so it is excluded.
SKIP_FILES = {".env", "mcp-config.local.json", "check_secrets.py"}


def candidate_files() -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=HERE, stderr=subprocess.DEVNULL, text=True,
        )
        files = [f for f in out.splitlines() if f]
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = []
        for root, dirs, names in os.walk(HERE):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in names:
                files.append(os.path.relpath(os.path.join(root, name), HERE))
    result = []
    for rel in files:
        if rel in SKIP_FILES or rel.startswith(".git/"):
            continue
        path = os.path.join(HERE, rel)
        if os.path.isfile(path):
            result.append(rel)
    return sorted(set(result))


def check_account_context() -> list[str]:
    problems: list[str] = []
    path = os.path.join(HERE, "api_map.json")
    if not os.path.isfile(path):
        return problems
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    ctx = data.get("verified_account_context") or {}
    for key in ("name", "ship_to_name", "ship_to_address", "email", "address"):
        if key in ctx:
            problems.append(f"api_map.json: verified_account_context.{key} must be removed")
    warehouse = str(ctx.get("warehouse_uuid_from_account") or "")
    if re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", warehouse):
        problems.append("api_map.json: warehouse_uuid_from_account contains a real UUID")
    return problems


def main() -> int:
    files = candidate_files()
    problems = check_account_context()
    for rel in files:
        path = os.path.join(HERE, rel)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for label, pattern in PATTERNS:
            for match in re.finditer(pattern, text):
                value = match.group(0)
                line = text.count("\n", 0, match.start()) + 1
                shown = value if label != "real email address" else "<redacted>"
                problems.append(f"{rel}:{line} {label}: {shown[:80]}")
    if ".env" in files:
        problems.append(".env is staged for publication")

    if problems:
        print("SECRET CHECK FAILED - do not push:")
        for problem in problems:
            print("  -", problem)
        return 1
    print(f"SECRET CHECK PASS - scanned {len(files)} publishable files, no credentials or account PII found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

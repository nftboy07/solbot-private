#!/usr/bin/env python3
"""Scan tracked source files for accidental secret leaks. Exit 1 if found."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Paths never scanned (examples/templates only)
SKIP_PREFIXES = (
    ".git/",
    "venv/",
    ".venv/",
    "__pycache__/",
)

SKIP_FILES = {
    ".env.example",
    "data/proxies.txt.example",
    "data/state.json.example",
    "tools/check_secrets.py",
}

PATTERNS = [
    (re.compile(r"http://[a-zA-Z0-9._-]+:[a-zA-Z0-9._-]+@\d"), "proxy URL with credentials"),
    (re.compile(r"p\.webshare\.io:\d+:[a-zA-Z0-9_-]+:[a-zA-Z0-9]+"), "webshare proxy credential"),
    (re.compile(r"(?i)(api[_-]?key|secret|password|private[_-]?key)\s*=\s*['\"][^'\"]{8,}['\"]"), "hardcoded credential assignment"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "PEM private key"),
    (re.compile(r"(?i)WALLET_PRIVATE_KEY\s*=\s*['\"]?[1-9A-HJ-NP-Za-km-z]{80,}"), "wallet private key in env"),
    (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "OpenAI-style API key"),
    (re.compile(r"AIza[0-9A-Za-z_-]{30,}"), "Google API key"),
    (re.compile(r"13\.201\.69\.107"), "production VPS IP"),
    (re.compile(r"REDACTED_SSH_KEY"), "SSH key path"),
]

BLOCKED_PATHS = {
    "data/proxies.txt",
    "data/state.json",
    ".env",
}


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [ROOT / line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    errors: list[str] = []

    for rel in BLOCKED_PATHS:
        if (ROOT / rel).exists():
            tracked = subprocess.run(
                ["git", "ls-files", "--error-unmatch", rel],
                cwd=ROOT,
                capture_output=True,
            )
            if tracked.returncode == 0:
                errors.append(f"BLOCKED file is tracked by git: {rel}")

    for path in tracked_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES:
            continue
        if any(rel.startswith(p) for p in SKIP_PREFIXES):
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern, label in PATTERNS:
            for match in pattern.finditer(text):
                snippet = match.group()[:40] + ("..." if len(match.group()) > 40 else "")
                errors.append(f"{rel}: {label} -> {snippet}")

    for e in errors:
        print(f"FAIL: {e}")

    if errors:
        print(f"\n{len(errors)} secret leak(s) detected. Remove before pushing to public repo.")
        return 1
    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
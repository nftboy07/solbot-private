"""Simple health probe for solbot.service."""

import json
import sys
from pathlib import Path


def main() -> int:
    log = Path("solbot.log")
    if not log.exists():
        print("log missing")
        return 1
    if log.stat().st_size > 2_000_000_000:
        print("log too large")
        return 1
    state = Path("data/state.json")
    if state.exists():
        try:
            json.loads(state.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("state.json corrupt")
            return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
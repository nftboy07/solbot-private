"""Remove positions that were bulk-imported from wallet (not bot snipes)."""

import json
import time
from pathlib import Path


def main():
    path = Path("data/state.json")
    if not path.exists():
        print("No state.json")
        return

    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)

    positions = state.get("positions", {})
    now = time.time()
    kept = {}
    removed = 0
    for mint, pos in positions.items():
        size = float(pos.get("size", 0) or 0)
        active = pos.get("active", True)
        start = float(pos.get("start_time", 0) or 0)
        symbol = pos.get("symbol", "")
        # Keep recent bot snipes (last 48h) or positions with SOL-sized entries
        recent = (now - start) < 172800 if start > 0 else False
        bot_like = size >= 0.001 and symbol not in ("SYNCED", "???", "")
        if active and (recent or bot_like):
            kept[mint] = pos
        else:
            removed += 1

    state["positions"] = kept
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    print(f"Kept {len(kept)} positions, removed {removed} wallet imports.")


if __name__ == "__main__":
    main()
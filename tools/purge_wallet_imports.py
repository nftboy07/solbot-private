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
    max_keep = int(__import__("os").getenv("MAX_STATE_POSITIONS", "15"))
    active_items = [
        (float(pos.get("start_time", 0) or 0), mint, pos)
        for mint, pos in positions.items()
        if pos.get("active", True)
    ]
    active_items.sort(key=lambda x: x[0], reverse=True)
    keep_mints = {mint for _, mint, _ in active_items[:max_keep]}
    kept = {}
    removed = 0
    for mint, pos in positions.items():
        if mint in keep_mints:
            kept[mint] = pos
        else:
            removed += 1

    state["positions"] = kept
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    print(f"Kept {len(kept)} positions, removed {removed} wallet imports.")


if __name__ == "__main__":
    main()
"""Remove ghost positions (active entries with zero size) from state.json."""

import json
from pathlib import Path


def main():
    path = Path("data/state.json")
    if not path.exists():
        print("No state.json found.")
        return

    with path.open("r", encoding="utf-8") as handle:
        state = json.load(handle)

    positions = state.get("positions", {})
    removed = []
    for mint, pos in list(positions.items()):
        size = float(pos.get("size", 0) or 0)
        active = pos.get("active", True)
        if active and size <= 0:
            pos["active"] = False
            removed.append(mint)

    if removed:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2)
        print(f"Deactivated {len(removed)} ghost positions.")
    else:
        print("No ghost positions found.")


if __name__ == "__main__":
    main()
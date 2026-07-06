"""Update .env trading limits. Run on the VPS with ENV_FILE set."""

import os
from pathlib import Path

ENV_FILE = Path(os.getenv("ENV_FILE", ".env"))


def main():
    if not ENV_FILE.exists():
        print(f"{ENV_FILE} not found")
        return

    lines = ENV_FILE.read_text(encoding="utf-8").splitlines(keepends=True)
    new_lines = []
    has_max_positions = False
    for line in lines:
        if line.startswith("MIN_MARKET_CAP_USD="):
            new_lines.append("MIN_MARKET_CAP_USD=100000\n")
        elif line.startswith("MAX_ACTIVE_POSITIONS="):
            new_lines.append("MAX_ACTIVE_POSITIONS=100\n")
            has_max_positions = True
        else:
            new_lines.append(line)

    if not has_max_positions:
        new_lines.append("MAX_ACTIVE_POSITIONS=100\n")

    ENV_FILE.write_text("".join(new_lines), encoding="utf-8")
    print(f"Updated {ENV_FILE} successfully")


if __name__ == "__main__":
    main()
"""Ensure SOLANA_RPC_POOL includes a public fallback RPC endpoint."""

import os
from pathlib import Path

FALLBACK = "https://api.mainnet-beta.solana.com"


def main():
    env_path = Path(".env")
    if not env_path.exists():
        print(".env not found")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = []
    pool_found = False
    changed = False

    for line in lines:
        if line.startswith("SOLANA_RPC_POOL="):
            pool_found = True
            value = line.split("=", 1)[1].strip()
            urls = [u.strip() for u in value.split(",") if u.strip()]
            if FALLBACK not in urls:
                urls.append(FALLBACK)
                changed = True
            updated.append("SOLANA_RPC_POOL=" + ",".join(urls))
        else:
            updated.append(line)

    if not pool_found:
        updated.append(f"SOLANA_RPC_POOL={FALLBACK}")
        changed = True

    if changed:
        env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        print("Added public RPC fallback to SOLANA_RPC_POOL")
    else:
        print("SOLANA_RPC_POOL already has fallback")


if __name__ == "__main__":
    main()
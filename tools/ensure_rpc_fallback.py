"""Ensure SOLANA_RPC_POOL and SOLANA_RPC_URL use a working public fallback."""

import os
from pathlib import Path

FALLBACK = "https://api.mainnet-beta.solana.com"


def main():
    env_path = Path(os.getenv("ENV_FILE", ".env"))
    if not env_path.exists():
        print(f"{env_path} not found")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = []
    pool_found = False
    rpc_found = False
    changed = False

    for line in lines:
        if line.startswith("SOLANA_RPC_POOL="):
            pool_found = True
            value = line.split("=", 1)[1].strip()
            urls = [u.strip() for u in value.split(",") if u.strip()]
            if FALLBACK not in urls:
                urls.insert(0, FALLBACK)
                changed = True
            elif urls[0] != FALLBACK:
                urls = [FALLBACK] + [u for u in urls if u != FALLBACK]
                changed = True
            updated.append("SOLANA_RPC_POOL=" + ",".join(urls))
        elif line.startswith("SOLANA_RPC_URL="):
            rpc_found = True
            if "quiknode" in line.lower() or "quicknode" in line.lower():
                updated.append(f"SOLANA_RPC_URL={FALLBACK}")
                changed = True
            else:
                updated.append(line)
        else:
            updated.append(line)

    if not pool_found:
        updated.append(f"SOLANA_RPC_POOL={FALLBACK}")
        changed = True
    if not rpc_found:
        updated.append(f"SOLANA_RPC_URL={FALLBACK}")
        changed = True

    if changed:
        env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        print(f"Updated {env_path}: public RPC is now primary fallback")
    else:
        print("RPC config already OK")


if __name__ == "__main__":
    main()
"""paste.trade integration client for Solbot."""

import os
import logging
from datetime import datetime, timezone
import aiohttp
from typing import Optional, Dict, Any

logger = logging.getLogger("solbot.paste_trade")


class PasteTradeClient:
    def __init__(self, key: Optional[str] = None, url: Optional[str] = None, handle: Optional[str] = None):
        # Fall back to env variables if not explicitly provided in config
        self.key = key or os.getenv("PASTE_TRADE_KEY") or os.getenv("PASTE_TRADE_API_KEY") or ""
        self.url = (url or os.getenv("PASTE_TRADE_URL") or os.getenv("BOARD_URL") or "https://paste.trade").rstrip("/")
        self.handle = handle or os.getenv("PASTE_TRADE_HANDLE") or "@solbot"
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def ensure_key(self) -> Optional[str]:
        """Ensure an API key exists. Auto-provisions a new one if missing."""
        if self.key:
            return self.key

        logger.info("[paste.trade] No API key found. Auto-provisioning your identity...")
        session = await self.get_session()
        try:
            async with session.post(f"{self.url}/api/keys", json={}) as res:
                if res.status != 200:
                    err_text = await res.text()
                    logger.error(f"[paste.trade] Failed to create API key ({res.status}): {err_text}")
                    return None

                result = await res.json()
                self.key = result.get("api_key", "")
                handle = result.get("handle", "")
                logger.info(f"[paste.trade] Successfully provisioned identity: @{handle} on {self.url}")

                # Save key back to .env files
                self._save_key_to_env(self.key)
                return self.key
        except Exception as e:
            logger.error(f"[paste.trade] Network error auto-provisioning key: {e}")
            return None

    def _save_key_to_env(self, key: str):
        """Append PASTE_TRADE_KEY to the project's .env file."""
        env_paths = [".env", "../.env"]
        line = f"\nPASTE_TRADE_KEY={key}\n"
        for path in env_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "PASTE_TRADE_KEY" not in content:
                        with open(path, "a", encoding="utf-8") as f:
                            f.write(line)
                        logger.info(f"[paste.trade] Saved provisioned API key to {path}")
                        return
                except Exception as e:
                    logger.debug(f"[paste.trade] Could not save to {path}: {e}")

    async def post_trade(self, ticker: str, direction: str, author_price: float, thesis: str, author_handle: Optional[str] = None) -> bool:
        """
        Post a trade idea to paste.trade.
        """
        api_key = await self.ensure_key()
        if not api_key:
            logger.warning("[paste.trade] API key is missing. Skipping trade post.")
            return False

        # Map buy/long -> long, sell/short -> short
        norm_dir = "long"
        if direction.lower() in ("sell", "short"):
            norm_dir = "short"

        payload = {
            "ticker": ticker.upper(),
            "direction": norm_dir,
            "thesis": thesis,
            "headline_quote": f"Solbot opened a {norm_dir} position on {ticker.upper()}",
            "author_handle": author_handle or self.handle,
            "author_price": author_price,
            "platform": "solana",
            "instrument": "spot",
            "trade_type": "spot",
            "author_date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        }

        session = await self.get_session()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        try:
            logger.info(f"[paste.trade] Submitting recommendation for {ticker} ({norm_dir}) at ${author_price:,.6f}")
            async with session.post(f"{self.url}/api/trades", json=payload, headers=headers) as res:
                text = await res.text()
                if res.status != 200:
                    logger.error(f"[paste.trade] Failed to submit trade ({res.status}): {text}")
                    return False

                # Handle potential warning messages in the response
                try:
                    res_json = await res.json()
                    warnings = res_json.get("warnings", [])
                    for warning in warnings:
                        logger.warning(f"[paste.trade API WARNING] {warning}")
                except Exception:
                    pass

                logger.info(f"[paste.trade] Trade successfully published to paste.trade: {text}")
                return True
        except Exception as e:
            logger.error(f"[paste.trade] Connection error while publishing trade: {e}")
            return False

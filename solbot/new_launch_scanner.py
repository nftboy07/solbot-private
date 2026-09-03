"""1-second REST poller for newest pump.fun launches (and optional Raydium grads).

PumpPortal's WebSocket (`subscribeNewToken`) is the primary real-time path.
This scanner is a same-process fallback so a dropped WS frame still surfaces
within ~SNIPER_SCAN_INTERVAL_SECONDS. It does not buy: it feeds the existing
`_schedule_token_evaluation` filter chain.

Verified live (2026-09-03):
  GET https://frontend-api-v3.pump.fun/coins
      ?offset=0&limit=20&sort=created_timestamp&order=DESC&includeNsfw=false
  returns a JSON list of coin objects (no auth required on this host).
  `complete=true` lists graduated coins (Raydium / pump AMM). Meteora is not
  a new-launch source in this repo — only arbitrage quotes it.
"""

from __future__ import annotations

import asyncio
import logging
from time import time
from typing import Any, Dict, Iterable, List, Optional, Set

from solbot.mayhem import metadata_indicates_mayhem
from solbot.models import TokenEvent

logger = logging.getLogger("bot.new_launch_scanner")

COINS_URL = "https://frontend-api-v3.pump.fun/coins"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def lamports_to_sol(raw: Any) -> float:
    """Convert pump.fun reserve fields that are often lamports (1e9)."""
    value = _as_float(raw, 0.0)
    if value > 1e6:
        return value / 1e9
    return value


def created_timestamp_seconds(raw: Any) -> float:
    value = _as_float(raw, 0.0)
    if value > 1e12:
        return value / 1000.0
    if value > 0:
        return value
    return time()


def extract_coin_list(payload: Any) -> List[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "coins", "results"):
            inner = payload.get(key)
            if isinstance(inner, list):
                return [item for item in inner if isinstance(item, dict)]
    return []


def token_event_from_pump_coin(coin: dict, sol_price: float = 150.0) -> Optional[TokenEvent]:
    mint = (coin.get("mint") or "").strip()
    if not mint:
        return None
    virtual_sol = lamports_to_sol(
        coin.get("virtual_sol_reserves") or coin.get("virtual_quote_reserves")
    )
    real_sol = lamports_to_sol(
        coin.get("real_sol_reserves") or coin.get("real_quote_reserves")
    )
    liquidity = virtual_sol if virtual_sol > 0 else real_sol
    usd_mcap = _as_float(coin.get("usd_market_cap") or coin.get("market_cap_usd"))
    if usd_mcap <= 0:
        mcap_sol = _as_float(coin.get("market_cap") or coin.get("market_cap_quote"))
        usd_mcap = mcap_sol * sol_price if sol_price > 0 else 0.0
    return TokenEvent(
        mint=mint,
        name=coin.get("name") or "Unknown",
        symbol=coin.get("symbol") or "???",
        uri=coin.get("metadata_uri") or coin.get("image_uri"),
        creator=coin.get("creator") or "",
        initial_buy_sol=0.0,
        market_cap_usd=usd_mcap,
        liquidity_sol=liquidity,
        timestamp=created_timestamp_seconds(coin.get("created_timestamp")),
    )


def should_skip_coin(coin: dict, sources: Iterable[str]) -> Optional[str]:
    """Cheap payload-level rejects before the full filter chain. Not a safety oracle."""
    source_set = {s.strip().lower() for s in sources if s}
    if coin.get("is_banned"):
        return "banned"
    if metadata_indicates_mayhem(coin):
        return "mayhem"
    complete = bool(coin.get("complete"))
    if complete and "raydium" not in source_set:
        return "graduated (raydium source disabled)"
    real_sol = lamports_to_sol(coin.get("real_sol_reserves") or coin.get("real_quote_reserves"))
    virtual_sol = lamports_to_sol(
        coin.get("virtual_sol_reserves") or coin.get("virtual_quote_reserves")
    )
    if virtual_sol <= 0 and real_sol <= 0:
        return "no LP"
    return None


class NewLaunchScanner:
    """Poll newest pump.fun coins at ~1s and enqueue them for the existing sniper."""

    def __init__(self, bot: Any):
        self.bot = bot
        self._running = False
        self._seen: Set[str] = set()
        self._session = None

    @property
    def interval(self) -> float:
        cfg = getattr(self.bot, "_config", None)
        sniper = getattr(cfg, "sniper", None) if cfg else None
        raw = getattr(sniper, "scan_interval_seconds", 1.0) if sniper else 1.0
        return max(0.25, float(raw or 1.0))

    @property
    def sources(self) -> List[str]:
        cfg = getattr(self.bot, "_config", None)
        sniper = getattr(cfg, "sniper", None) if cfg else None
        raw = getattr(sniper, "sources", None) if sniper else None
        if raw:
            return [s.lower() for s in raw]
        return ["pumpfun", "raydium"]

    async def start_monitoring(self):
        if not getattr(getattr(self.bot, "_config", None), "sniper", None) or not self.bot._config.sniper.enabled:
            logger.info("New-launch scanner disabled (SNIPER_ENABLED=false)")
            return
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            logger.warning("curl_cffi unavailable; new-launch REST scanner not started")
            return

        self._running = True
        logger.info(
            "New-launch scanner started | interval=%.2fs | sources=%s | url=%s",
            self.interval, ",".join(self.sources), COINS_URL,
        )
        async with AsyncSession(impersonate="chrome120") as session:
            self._session = session
            while self._running:
                try:
                    await self._poll_once()
                except Exception as exc:
                    logger.error("New-launch scanner error: %s", exc)
                await asyncio.sleep(self.interval)

    async def stop(self):
        self._running = False
        logger.info("New-launch scanner stopped")

    async def _poll_once(self):
        queries = [
            {"offset": "0", "limit": "20", "sort": "created_timestamp", "order": "DESC", "includeNsfw": "false"},
        ]
        if "raydium" in self.sources:
            queries.append({
                "offset": "0",
                "limit": "10",
                "sort": "created_timestamp",
                "order": "DESC",
                "includeNsfw": "false",
                "complete": "true",
            })
        for params in queries:
            coins = await self._fetch_coins(params)
            await self._ingest(coins)

    async def _fetch_coins(self, params: Dict[str, str]) -> List[dict]:
        if not self._session:
            return []
        proxy = getattr(getattr(self.bot, "_config", None), "proxy_url", None) or None
        resp = await self._session.get(COINS_URL, params=params, proxy=proxy, timeout=8)
        if resp.status_code != 200:
            logger.debug("New-launch poll HTTP %s params=%s", resp.status_code, params)
            return []
        try:
            payload = resp.json()
        except Exception as exc:
            logger.debug("New-launch poll JSON error: %s", exc)
            return []
        return extract_coin_list(payload)

    async def _ingest(self, coins: List[dict]):
        sol_price = 150.0
        telegram = getattr(self.bot, "_telegram", None)
        if telegram and getattr(telegram, "_sol_price", 0) > 0:
            sol_price = float(telegram._sol_price)

        for coin in coins:
            mint = (coin.get("mint") or "").strip()
            if not mint or mint in self._seen:
                continue
            skip = should_skip_coin(coin, self.sources)
            if skip:
                self._seen.add(mint)
                logger.debug("Scanner skip %s: %s", mint[:8], skip)
                continue
            if mint in getattr(self.bot, "_processed_mints", set()):
                self._seen.add(mint)
                continue
            if mint in getattr(self.bot, "_pending_evaluations", set()):
                continue
            if mint in getattr(self.bot, "_positions", {}):
                self._seen.add(mint)
                continue

            token = token_event_from_pump_coin(coin, sol_price=sol_price)
            if not token:
                continue
            self._seen.add(mint)
            if len(self._seen) > 5000:
                # Bound memory: drop oldest-ish by rebuilding from the tail.
                self._seen = set(list(self._seen)[-2500:])
            reason = "Raydium graduation" if coin.get("complete") else "pump.fun new launch"
            logger.info(
                "Scanner %s: %s (%s) liq=%.2f SOL mcap=$%.0f",
                reason, token.symbol, mint[:8], token.liquidity_sol, token.market_cap_usd,
            )
            asyncio.create_task(self.bot._schedule_token_evaluation(token, coin))

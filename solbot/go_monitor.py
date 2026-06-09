"""Asynchronous monitor for Pump.fun GO bounties with Updated API Endpoints and anti-spam protection."""

import asyncio
import aiohttp
import re
from typing import Set, List, Dict, Any, Optional
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("go_monitor")

class GoMonitor:
    """Monitors pump.fun/go for high-reward bounties and snipes associated tokens."""

    def __init__(self, bot, reward_threshold: float = 5.0, poll_interval: int = 15):
        self._bot = bot
        self._reward_threshold = reward_threshold  # in SOL
        self._poll_interval = poll_interval
        # Using frontend-api which is the standard endpoint for Go Bounties
        self._api_url = "https://frontend-api.pump.fun/go/bounties"
        self._seen_bounties: Set[str] = set()
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._has_failed = False  # Track failure to stop log spam

    def _sanitize_proxy(self, proxy: str) -> str:
        """Hide password in proxy URL for safe logging."""
        if not proxy: return "None"
        return re.sub(r"://.*@", "://***:***@", proxy)

    async def start_monitoring(self):
        """Main loop for tracking new bounties."""
        self._running = True
        logger.info(f"Starting Pump.fun GO Monitor (Threshold: {self._reward_threshold} SOL)")
        
        # Standard browser headers to try and bypass simple Cloudflare blocks
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://pump.fun/go",
            "Origin": "https://pump.fun"
        }
        
        async with aiohttp.ClientSession(headers=headers) as session:
            self._session = session
            while self._running:
                if self._has_failed:
                    # If it failed once, we idle to prevent log spam
                    await asyncio.sleep(3600)
                    continue

                try:
                    await self._poll_bounties()
                except Exception as e:
                    logger.error(f"Unexpected error in GO monitor loop: {e}")
                    # If it's a connection error or similar, we also treat it as a failure state
                    self._has_failed = True
                    logger.warning("Pump.fun GO Monitor entering idle state due to connection error.")
                
                await asyncio.sleep(self._poll_interval)

    async def stop(self):
        """Stop the monitor."""
        self._running = False
        logger.info("Pump.fun GO Monitor stopped")

    async def _poll_bounties(self):
        """Fetch and process active bounties from the pump.fun GO API."""
        params = {
            "offset": 0,
            "limit": 20,
            "sort": "reward",
            "order": "desc"
        }
        
        proxy = self._bot._config.proxy_url if self._bot._config.proxy_url else None
        logger.debug(f"Polling GO bounties. Proxy: {self._sanitize_proxy(proxy)}")
        
        async with self._session.get(self._api_url, params=params, timeout=10, proxy=proxy) as response:
            if response.status != 200:
                # Handle ANY HTTP error (530, 403, 404, etc.) as a permanent failure for this session
                self._has_failed = True
                logger.warning(
                    f"Pump.fun GO Monitor disabled: Endpoint returned HTTP {response.status} "
                    "(likely Cloudflare blocking AWS Lightsail IP). Monitor will now idle."
                )
                return
            
            data = await response.json()
            if not isinstance(data, list):
                logger.error("Bounty API returned unexpected format")
                return

            for bounty in data:
                await self._process_bounty(bounty)

    async def _process_bounty(self, bounty: Dict[str, Any]):
        """Evaluate a single bounty and trigger sniping if it passes filters."""
        bounty_id = str(bounty.get("id"))
        reward_lamports = bounty.get("reward_amount", 0)
        reward_sol = reward_lamports / 1e9
        token_mint = bounty.get("mint")

        if bounty_id in self._seen_bounties:
            return

        if reward_sol >= self._reward_threshold and token_mint:
            logger.info(f"Found high-reward bounty: {bounty_id} | Reward: {reward_sol} SOL | Mint: {token_mint}")
            
            # Fetch actual token metadata to avoid mock valuation
            meta = await self._bot._pump_client.get_token_metadata(token_mint)
            mcap_usd = float(meta.get("market_cap_sol", 0)) * self._bot._telegram._sol_price

            token = TokenEvent(
                mint=token_mint,
                name=meta.get("name", bounty.get("name", "GO Bounty")),
                symbol=meta.get("symbol", bounty.get("symbol", "GO")),
                creator=meta.get("creator", "GO_ESCROW"),
                market_cap_usd=mcap_usd,
                liquidity_sol=float(meta.get("liquidity_sol", 0)),
                timestamp=asyncio.get_event_loop().time()
            )

            # AI Filtering logic
            if self._bot._ai_enabled:
                token_data = {
                    'mint': token.mint,
                    'symbol': token.symbol,
                    'description': bounty.get("description", ""),
                    'creator': token.creator
                }
                score = await self._bot._ai_filter.score_token(token_data)
                if score < self._bot._ai_min_score:
                    logger.warning(f"AI score {score} < {self._bot._ai_min_score}, skipping GO token {token.symbol}")
                    self._seen_bounties.add(bounty_id)
                    return

            # Trigger Snipe
            logger.info(f"GO Bounty token {token_mint} passed filters. Sniping...")
            asyncio.create_task(self._bot._execute_snipe(token, self._bot._config.jupiter.buy_amount_sol, f"GO Bounty Sniper ({reward_sol} SOL)"))
            
            self._seen_bounties.add(bounty_id)

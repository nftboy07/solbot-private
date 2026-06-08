"""Asynchronous monitor for Pump.fun GO bounties."""

import asyncio
import aiohttp
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
        self._api_url = "https://frontend-api.pump.fun/go/bounties"
        self._seen_bounties: Set[str] = set()
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None

    async def start_monitoring(self):
        """Main loop for tracking new bounties."""
        self._running = True
        logger.info(f"Starting Pump.fun GO Monitor (Threshold: {self._reward_threshold} SOL)")
        
        async with aiohttp.ClientSession() as session:
            self._session = session
            while self._running:
                try:
                    await self._poll_bounties()
                except Exception as e:
                    logger.error(f"Error in GO monitor loop: {e}")
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
        
        async with self._session.get(self._api_url, params=params, timeout=10) as response:
            if response.status != 200:
                logger.error(f"Failed to fetch bounties: HTTP {response.status}")
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
            
            # Create a mock TokenEvent for processing
            token = TokenEvent(
                mint=token_mint,
                name=bounty.get("name", "GO Bounty Token"),
                symbol=bounty.get("symbol", "GO"),
                creator="GO_ESCROW",
                market_cap_usd=0.0, # Will be fetched or updated later
                liquidity_sol=0.0,
                timestamp=asyncio.get_event_loop().time()
            )

            # AI Filtering logic
            if self._bot._ai_enabled:
                token_data = {
                    'mint': token.mint,
                    'symbol': token.symbol,
                    'description': bounty.get("description", "")
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

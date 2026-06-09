import asyncio
import re
import logging
import time
import os
from typing import List, Set, Optional, Dict, Any
import aiohttp
from solbot.models import TokenEvent

logger = logging.getLogger("bot.twitter_agents")

class TwitterAgentMonitor:
    """Monitors Top AI Trading Agents for Alpha using SocialData API."""

    def __init__(self, bot_instance, api_key: Optional[str] = None):
        self._bot = bot_instance
        self._api_key = api_key or os.getenv("SOCIALDATA_API_KEY")
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Core agents to monitor
        self._target_handles = ["aixbt_agent", "grok", "truth_terminal", "ai16z"]
        self._last_seen_ids: Dict[str, str] = {}
        
        # Regex for CAs and pump.fun
        self._mint_regex = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
        self._pump_regex = re.compile(r"pump\.fun/coin/([1-9A-HJ-NP-Za-km-z]{32,44})")

    async def start(self):
        if not self._api_key:
            logger.warning("TwitterAgentMonitor: No SOCIALDATA_API_KEY found. Skipping.")
            return
            
        self._running = True
        self._session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self._api_key}"})
        asyncio.create_task(self._poll_loop())
        logger.info(f"Twitter AI Agent Monitor started for handles: {self._target_handles}")

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()

    async def _poll_loop(self):
        while self._running:
            for handle in self._target_handles:
                try:
                    tweets = await self._fetch_tweets(handle)
                    await self._process_tweets(handle, tweets)
                except Exception as e:
                    logger.error(f"Error monitoring AI Agent @{handle}: {e}")
                await asyncio.sleep(10) # Avoid hitting SocialData rate limits too hard
            await asyncio.sleep(60)

    async def _fetch_tweets(self, handle: str) -> List[Dict]:
        url = f"https://api.socialdata.tools/twitter/user/{handle}/tweets"
        async with self._session.get(url) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("tweets", [])
            elif resp.status == 429:
                logger.warning("SocialData Rate Limit hit.")
            return []

    async def _process_tweets(self, handle: str, tweets: List[Dict]):
        if not tweets: return
        
        last_id = self._last_seen_ids.get(handle)
        # Update checkpoint for next run
        current_top_id = str(tweets[0].get("id_str") or tweets[0].get("id"))
        self._last_seen_ids[handle] = current_top_id
        
        if not last_id: return # First run, just baseline

        for tweet in tweets:
            t_id = str(tweet.get("id_str") or tweet.get("id"))
            if t_id == last_id: break
            
            text = tweet.get("full_text") or tweet.get("text", "")
            
            # Find Mint
            pump_match = self._pump_regex.search(text)
            mint = pump_match.group(1) if pump_match else None
            
            if not mint:
                mints = self._mint_regex.findall(text)
                for m in mints:
                    if m != "So11111111111111111111111111111111111111112":
                        mint = m
                        break
            
            if mint:
                logger.warning(f"🚨 AI AGENT ALPHA: @{handle} mentioned {mint}")
                asyncio.create_task(self._handle_alpha(mint, handle, text))

    async def _handle_alpha(self, mint: str, handle: str, text: str):
        # 1. Health Check with GeckoTerminal
        is_safe = await self._bot._gecko.evaluate_safety(mint)
        
        if not is_safe:
            await self._bot._telegram.send_message(
                f"⚠️ <b>AI Agent Mentions Low-Liq Token</b>\n"
                f"Source: @{handle}\n"
                f"Mint: <code>{mint}</code>\n"
                f"Status: Skipped (Low Liquidity)"
            )
            return

        # 2. Qualified? Execute Snipe
        token = TokenEvent(
            mint=mint,
            name=f"AI Agent: @{handle}",
            symbol="ALPHA",
            creator="twitter_ai",
            market_cap_usd=0,
            liquidity_sol=0,
            timestamp=time.time()
        )
        
        # Trigger buy if autobuy is on, otherwise alert
        if self._bot._autobuy_enabled:
            await self._bot._execute_snipe(token, self._bot._config.jupiter.buy_amount_sol, f"AI Agent Alpha (@{handle})")
        else:
            await self._bot._telegram.send_message(
                f"🌟 <b>HIGH SIGNAL ALPHA</b>\n"
                f"Source: @{handle}\n"
                f"Mint: <code>{mint}</code>\n"
                f"Action: Auto-buy is OFF. Review manually."
            )

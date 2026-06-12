"""Twitter (X) Monitor for Solbot with Scraping and API support."""

import asyncio
import re
import logging
import time
import os
from typing import List, Set, Optional, Dict
import aiohttp
from solbot.config import BotConfig
from solbot.models import TokenEvent

logger = logging.getLogger("bot.twitter")

class TwitterMonitor:
    """Monitors specific Twitter handles for Solana mints and pump.fun links."""

    def __init__(self, config: BotConfig, bot_instance):
        self._config = config
        self._bot = bot_instance
        self._handles: Set[str] = set()
        self._default_handles = ["blknoiz06", "hunterbiden", "weremeow", "solana_daily", "crypto_bitlord"]
        self._last_seen_ids: Dict[str, str] = {}
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Regex for Solana addresses and pump.fun links
        self._mint_regex = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
        self._pump_regex = re.compile(r"pump\.fun/coin/([1-9A-HJ-NP-Za-km-z]{32,44})")
        
        # API Config
        self._api_key = os.getenv("TWITTER_API_KEY") or os.getenv("SOCIALDATA_API_KEY")
        # Mode is 'api' if we have a key, otherwise we fallback to a scraper mode
        self._mode = "api" if self._api_key else "scraper"

    async def start(self):
        self._running = True
        timeout = aiohttp.ClientTimeout(total=15)
        self._session = aiohttp.ClientSession(timeout=timeout)
        
        # Auto-track default KOL handles
        for h in self._default_handles:
            self.add_handle(h)
            
        asyncio.create_task(self._poll_loop())
        logger.info(f"Twitter Monitor started in {self._mode} mode.")

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()

    def add_handle(self, handle: str):
        handle = handle.lstrip("@").lower()
        self._handles.add(handle)
        logger.info(f"Tracking Twitter handle: @{handle}")

    def remove_handle(self, handle: str):
        handle = handle.lstrip("@").lower()
        if handle in self._handles:
            self._handles.remove(handle)
            logger.info(f"Stopped tracking Twitter handle: @{handle}")

    async def _poll_loop(self):
        while self._running:
            if not self._handles:
                await asyncio.sleep(10)
                continue
            
            for handle in list(self._handles):
                try:
                    tweets = await self._fetch_tweets(handle)
                    await self._process_tweets(handle, tweets)
                except Exception as e:
                    logger.error(f"Error polling Twitter @{handle}: {e}")
                
                # Jitter/Delay between handles to avoid rate limits
                await asyncio.sleep(5)
            
            await asyncio.sleep(15)

    async def _fetch_tweets(self, handle: str) -> List[Dict]:
        """Fetch tweets using SocialData API or SocialData-compatible provider."""
        if self._mode == "api":
            # Using SocialData API endpoint for user tweets
            url = f"https://api.socialdata.tools/twitter/user/{handle}/tweets"
            headers = {"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"}
            try:
                async with self._session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("tweets", [])
                    else:
                        logger.error(f"Twitter API error for @{handle}: {resp.status}")
                        return []
            except Exception as e:
                logger.error(f"Failed to fetch tweets for @{handle}: {e}")
                return []
        else:
            # Placeholder for alternative scraper integration (e.g. BluesMinds or Apify)
            return []

    async def _process_tweets(self, handle: str, tweets: List[Dict]):
        if not tweets: return
        
        last_id = self._last_seen_ids.get(handle)
        new_tweets = []
        
        for tweet in tweets:
            t_id = str(tweet.get("id_str") or tweet.get("id"))
            if t_id == last_id: break
            new_tweets.append(tweet)
            
        if not new_tweets: return
        
        # Update checkpoint
        self._last_seen_ids[handle] = str(new_tweets[0].get("id_str") or new_tweets[0].get("id"))
        
        for tweet in reversed(new_tweets):
            text = tweet.get("full_text") or tweet.get("text", "")
            
            # 1. Check for pump.fun links first
            pump_match = self._pump_regex.search(text)
            mint = pump_match.group(1) if pump_match else None
            
            # 2. Check for raw mint addresses
            if not mint:
                mints = self._mint_regex.findall(text)
                # Filter for valid looking mints (exclude common false positives)
                for m in mints:
                    if m != "So11111111111111111111111111111111111111112": # Skip SOL
                        mint = m
                        break
            
            if mint:
                logger.info(f"🐦 Twitter Match (@{handle}): Found Mint {mint}")
                # AI Filter integration
                if self._bot._ai_enabled:
                    token_data = {
                        "mint": mint,
                        "symbol": "TWEET",
                        "name": f"Twitter: @{handle}",
                        "creator": "twitter",
                        "sentiment_text": text
                    }
                    score = await self._bot._ai_filter.score_token(token_data)
                    logger.info(f"🤖 AI Score for {mint}: {score}")
                    if score < self._bot._ai_min_score:
                        logger.warning(f"❌ AI score {score} < {self._bot._ai_min_score}, skipping {mint}")
                        await self._bot._telegram.send_message(f"🚫 <b>AI Filtered: {mint}</b> (Score: {score})")
                        continue

                # Trigger sniping logic directly
                token = TokenEvent(
                    mint=mint,
                    name=f"Twitter: @{handle}",
                    symbol="TWEET",
                    creator="twitter",
                    market_cap_usd=0, # Unknown at this stage
                    liquidity_sol=0,
                    timestamp=time.time()
                )
                asyncio.create_task(self._bot._execute_snipe(token, self._config.jupiter.buy_amount_sol, f"Twitter (@{handle})"))

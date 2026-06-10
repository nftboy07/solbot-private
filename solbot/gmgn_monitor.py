import asyncio
import logging
from typing import Set, List, Dict, Any
from solbot.gmgn_client import GMGNClient
from solbot.models import TokenEvent

logger = logging.getLogger("bot.gmgn_monitor")

class GMGNMonitor:
    """Monitor for GMGN.ai live data feeds."""

    def __init__(self, bot, poll_interval: int = 30):
        self.bot = bot
        self.poll_interval = poll_interval
        self.client = GMGNClient()
        self.seen_mints: Set[str] = set()
        self._running = False

    async def start(self):
        self._running = True
        logger.info("GMGN Monitor started")
        while self._running:
            try:
                await self._poll_new_tokens()
                await self._poll_smart_money()
            except Exception as e:
                logger.error(f"Error in GMGN monitor: {e}")
            await asyncio.sleep(self.poll_interval)

    async def stop(self):
        self._running = False
        await self.client.close()
        logger.info("GMGN Monitor stopped")

    async def _poll_new_tokens(self):
        tokens = await self.client.get_new_tokens()
        for t in tokens:
            mint = t.get("address")
            if not mint or mint in self.seen_mints:
                continue
            
            self.seen_mints.add(mint)
            symbol = t.get('symbol', 'UNKNOWN')
            mcap = float(t.get('market_cap', 0))
            
            if mcap > 10000: # Example threshold
                msg = (
                    f"🌟 <b>GMGN Discovery</b>\n"
                    f"Token: {symbol}\n"
                    f"MCap: ${mcap:,.0f}\n"
                    f"Mint: <code>{mint}</code>"
                )
                if self.bot._telegram:
                    await self.bot._telegram.send_message(msg)

    async def _poll_smart_money(self):
        tokens = await self.client.get_smart_money_inflow()
        for t in tokens:
            # Logic for smart money alerts
            pass

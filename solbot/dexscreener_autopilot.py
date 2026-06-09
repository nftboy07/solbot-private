import asyncio
import aiohttp
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("dex_autopilot")

class DexScreenerAutopilot:
    """Autopilot for sniping DexScreener breakouts."""
    def __init__(self, bot):
        self._bot = bot
        self._trending_url = "https://api.dexscreener.com/token-profiles/latest/v1"
        self._running = False

    async def start_polling(self):
        self._running = True
        logger.info("DexScreener Breakout Autopilot started")
        async with aiohttp.ClientSession() as session:
            while self._running:
                try:
                    async with session.get(self._trending_url) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for token in data:
                                await self._evaluate_breakout(token)
                except Exception as e:
                    logger.error(f"Autopilot error: {e}")
                await asyncio.sleep(60)

    async def _evaluate_breakout(self, token_data):
        mint = token_data.get("tokenAddress")
        if not mint or mint in self._bot._positions:
            return

        # Basic momentum filter (simulated logic)
        if self._bot._ai_enabled:
            score = await self._bot._ai_filter.score_token(token_data)
            if score >= self._bot._ai_min_score:
                logger.info(f"Breakout detected: {token_data.get('symbol')} (Score: {score})")
                token = TokenEvent(
                    mint=mint,
                    name=token_data.get("name"),
                    symbol=token_data.get("symbol"),
                    creator="DexScreener_Breakout",
                    market_cap_usd=0.0,
                    liquidity_sol=0.0,
                    timestamp=asyncio.get_event_loop().time()
                )
                asyncio.create_task(self._bot._execute_snipe(token, 0.1, "DexScreener Breakout"))

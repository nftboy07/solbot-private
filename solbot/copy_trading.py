import asyncio
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("copy_trading")

class MempoolCopyTrader:
    """Monitors whale wallets on-chain to mirror trades."""
    def __init__(self, bot):
        self._bot = bot
        self._running = False

    async def start_monitoring(self):
        self._running = True
        logger.info(f"Mempool Copy-Trading started for {len(self._bot._filter._copy_targets)} whales")
        # Implementation would use a websocket subscription to accountSubscribe
        # for all addresses in self._bot._filter._copy_targets

    async def _handle_onchain_buy(self, whale_address, mint, amount_sol):
        # Blacklist check
        if self._bot.is_blacklisted(whale_address):
            logger.warning(f"Ignoring copy trade from blacklisted whale: {whale_address}")
            return

        logger.info(f"Whale {whale_address} bought {mint}. Mirroring...")
        token = TokenEvent(
            mint=mint,
            name="Mirror Trade",
            symbol="COPY",
            creator=whale_address,
            market_cap_usd=0.0,
            liquidity_sol=0.0,
            timestamp=asyncio.get_event_loop().time()
        )
        await self._bot._execute_snipe(token, self._bot._config.jupiter.buy_amount_sol, f"Copy-Trade [{whale_address[:4]}]")

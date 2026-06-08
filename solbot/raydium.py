import asyncio
from solbot.logger import get_logger
from solbot.models import TokenEvent

logger = get_logger("raydium")

class RaydiumClient:
    """Monitors Raydium for migrations from pump.fun."""
    def __init__(self, bot):
        self._bot = bot
        self._program_id = "675kPX9M4SG3GCEgJg5ky3u5wCcN1xeeAzg48b161G3"
        self._running = False

    async def start(self):
        self._running = True
        logger.info("Raydium Migration Monitor started")
        
    async def stop(self):
        self._running = False
        logger.info("Raydium Migration Monitor stopped")

    async def handle_migration_event(self, mint: str, name: str, symbol: str):
        """Triggered when a pump.fun token migrates to Raydium."""
        logger.info(f"Migration detected for {symbol} ({mint}). Sniping on Raydium...")
        token = TokenEvent(
            mint=mint,
            name=name,
            symbol=symbol,
            creator="PUMP_MIGRATION",
            market_cap_usd=0.0,
            liquidity_sol=0.0,
            timestamp=asyncio.get_event_loop().time()
        )
        await self._bot._execute_snipe(token, self._bot._config.jupiter.buy_amount_sol, "Raydium Migration Snipe")
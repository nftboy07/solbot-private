"""Hummingbot-style Pure Market Making (PMM) and Grid Trading Engine for Solana."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from solbot.config import HummingbotConfig

logger = logging.getLogger("bot.hummingbot_pmm")


@dataclass
class PMMOrderProposal:
    """Calculated buy (bid) and sell (ask) price and size proposals."""
    mid_price: float
    bid_price: float
    ask_price: float
    bid_amount_sol: float
    ask_amount_token: float
    inventory_skew: float
    grid_bids: List[Dict[str, float]] = field(default_factory=list)
    grid_asks: List[Dict[str, float]] = field(default_factory=list)


@dataclass
class PMMSession:
    """Active Pure Market Making session state for a token."""
    mint: str
    symbol: str
    base_spread_bps: int
    order_amount_sol: float
    grid_levels: int = 1
    target_inventory_ratio: float = 0.5
    created_at: float = field(default_factory=time.time)
    last_refresh_at: float = 0.0
    active: bool = True
    total_trades: int = 0
    realized_pnl_sol: float = 0.0
    inventory_tokens: float = 0.0
    inventory_sol: float = 0.0


class HummingbotPMMManager:
    """Manages Pure Market Making and Grid Trading sessions for Solbot."""

    def __init__(self, bot, config: Optional[HummingbotConfig] = None):
        self._bot = bot
        self._config = config or bot._config.hummingbot
        self._sessions: Dict[str, PMMSession] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self):
        """Start the PMM manager background daemon."""
        if not self._config.enabled:
            logger.info("Hummingbot PMM engine is disabled.")
            return
        self._running = True
        logger.info("Hummingbot PMM & Grid Trading engine initialized.")

    async def stop(self):
        """Stop all active PMM sessions."""
        self._running = False
        for mint, task in list(self._tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        self._sessions.clear()
        logger.info("Hummingbot PMM engine stopped.")

    def calculate_order_proposals(
        self,
        mid_price: float,
        base_spread_bps: int,
        order_amount_sol: float,
        current_token_balance: float,
        current_sol_balance: float,
        target_ratio: float = 0.5,
        grid_levels: int = 1,
    ) -> PMMOrderProposal:
        """
        Calculate inventory-skewed bid and ask prices and multi-level grid tiers.
        
        Inventory Skew logic:
        - Ratio of (token_value_in_sol) / (total_portfolio_value_in_sol).
        - If holding more token than target: skew > 0 -> bids widen, asks tighten to de-risk.
        - If holding less token than target: skew < 0 -> bids tighten, asks widen to accumulate.
        """
        if mid_price <= 0:
            return PMMOrderProposal(
                mid_price=0.0,
                bid_price=0.0,
                ask_price=0.0,
                bid_amount_sol=order_amount_sol,
                ask_amount_token=0.0,
                inventory_skew=0.0,
            )

        token_val_sol = current_token_balance * mid_price
        total_val_sol = max(token_val_sol + current_sol_balance, 0.0001)
        current_ratio = token_val_sol / total_val_sol
        raw_skew = current_ratio - target_ratio
        # Clamp inventory skew to [-0.8, 0.8]
        skew = max(-0.8, min(0.8, raw_skew))

        half_spread_pct = (base_spread_bps / 10000.0) / 2.0
        # Adjust spreads by inventory skew
        bid_spread_pct = half_spread_pct * (1.0 + skew)
        ask_spread_pct = half_spread_pct * (1.0 - skew)

        bid_price = mid_price * (1.0 - bid_spread_pct)
        ask_price = mid_price * (1.0 + ask_spread_pct)

        ask_amount_token = (order_amount_sol / ask_price) if ask_price > 0 else 0.0

        # Multi-level grid orders
        grid_bids = []
        grid_asks = []
        for level in range(1, grid_levels + 1):
            mult = float(level)
            b_price = mid_price * (1.0 - bid_spread_pct * mult)
            a_price = mid_price * (1.0 + ask_spread_pct * mult)
            b_amt = order_amount_sol / grid_levels
            a_amt = (b_amt / a_price) if a_price > 0 else 0.0
            grid_bids.append({"price": b_price, "amount_sol": b_amt})
            grid_asks.append({"price": a_price, "amount_tokens": a_amt})

        return PMMOrderProposal(
            mid_price=mid_price,
            bid_price=bid_price,
            ask_price=ask_price,
            bid_amount_sol=order_amount_sol,
            ask_amount_token=ask_amount_token,
            inventory_skew=skew,
            grid_bids=grid_bids,
            grid_asks=grid_asks,
        )

    async def start_session(
        self,
        mint: str,
        symbol: str = "TOKEN",
        base_spread_bps: Optional[int] = None,
        order_amount_sol: Optional[float] = None,
        grid_levels: int = 1,
    ) -> PMMSession:
        """Start a new PMM session for a token mint."""
        spread_bps = base_spread_bps or self._config.pmm_default_spread_bps
        amount_sol = order_amount_sol or self._config.pmm_max_inventory_sol

        if mint in self._sessions:
            session = self._sessions[mint]
            session.base_spread_bps = spread_bps
            session.order_amount_sol = amount_sol
            session.grid_levels = grid_levels
            session.active = True
            logger.info("Updated existing PMM session for %s (%s)", symbol, mint[:8])
            return session

        session = PMMSession(
            mint=mint,
            symbol=symbol,
            base_spread_bps=spread_bps,
            order_amount_sol=amount_sol,
            grid_levels=grid_levels,
        )
        self._sessions[mint] = session

        task = asyncio.create_task(self._session_loop(session), name=f"pmm-{mint[:8]}")
        self._tasks[mint] = task
        logger.info("Launched Hummingbot PMM session for %s (%s) spread=%sbps", symbol, mint[:8], spread_bps)
        return session

    async def stop_session(self, mint: str) -> bool:
        """Stop an active PMM session for a token mint."""
        if mint not in self._sessions:
            return False
        session = self._sessions.pop(mint)
        session.active = False
        if mint in self._tasks:
            task = self._tasks.pop(mint)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("Stopped PMM session for %s", mint[:8])
        return True

    def get_sessions(self) -> List[PMMSession]:
        """Get all active PMM sessions."""
        return list(self._sessions.values())

    async def _session_loop(self, session: PMMSession):
        """Periodic loop executing order proposals and rebalancing for a session."""
        while session.active and self._running:
            try:
                # 1. Fetch current price
                price = await self._fetch_token_price(session.mint)
                if price and price > 0:
                    proposal = self.calculate_order_proposals(
                        mid_price=price,
                        base_spread_bps=session.base_spread_bps,
                        order_amount_sol=session.order_amount_sol,
                        current_token_balance=session.inventory_tokens,
                        current_sol_balance=session.inventory_sol,
                        grid_levels=session.grid_levels,
                    )
                    session.last_refresh_at = time.time()
                    logger.debug(
                        "PMM [%s] Mid=%.6f Bid=%.6f Ask=%.6f Skew=%.2f",
                        session.symbol,
                        proposal.mid_price,
                        proposal.bid_price,
                        proposal.ask_price,
                        proposal.inventory_skew,
                    )

                refresh_interval = max(self._config.pmm_order_refresh_seconds, 3.0)
                await asyncio.sleep(refresh_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in PMM session loop for %s: %s", session.mint[:8], e)
                await asyncio.sleep(5.0)

    async def _fetch_token_price(self, mint: str) -> Optional[float]:
        """Fetch real-time price from PumpFunClient, JupiterClient, or Gateway."""
        if hasattr(self._bot, "_pumpfun_client") and self._bot._pumpfun_client:
            try:
                meta = await self._bot._pumpfun_client.get_token_metadata(mint)
                if meta and "price" in meta and meta["price"]:
                    return float(meta["price"])
            except Exception:
                pass
        return None

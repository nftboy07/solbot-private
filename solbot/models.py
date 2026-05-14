"""Data models for Solbot."""

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Optional


class TokenStatus(Enum):
    NEW = "new"
    QUALIFIED = "qualified"
    BLACKLISTED = "blacklisted"
    BUYING = "buying"
    HOLDING = "holding"
    SELLING = "selling"
    SOLD = "sold"
    REJECTED = "rejected"


class PositionStatus(Enum):
    """Status of a tracked position."""
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


class SellReason(Enum):
    """Why a position was sold."""
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT_1 = "take_profit_1"
    TAKE_PROFIT_2 = "take_profit_2"
    TAKE_PROFIT_3 = "take_profit_3"
    TRAILING_STOP = "trailing_stop"
    MANUAL = "manual"
    EMERGENCY = "emergency"
    RUG_DETECTED = "rug_detected"


@dataclass
class TokenEvent:
    """Represents a new token event from Pump.fun."""
    mint: str
    name: str
    symbol: str
    uri: Optional[str] = None
    creator: Optional[str] = None
    initial_buy_sol: float = 0.0
    market_cap_usd: float = 0.0
    liquidity_sol: float = 0.0
    timestamp: float = field(default_factory=time)

    @property
    def age_seconds(self) -> float:
        return time() - self.timestamp


@dataclass
class SwapQuote:
    """Represents a Jupiter swap quote."""
    input_mint: str
    output_mint: str
    in_amount: int
    out_amount: int
    price_impact_pct: float
    slippage_bps: int
    route_plan: list = field(default_factory=list)


@dataclass
class TradeResult:
    """Represents the result of a trade execution."""
    success: bool
    token_mint: str
    side: str = "buy"  # "buy" or "sell"
    tx_signature: Optional[str] = None
    amount_in: float = 0.0
    amount_out: float = 0.0
    error: Optional[str] = None
    latency_ms: float = 0.0


@dataclass
class SellOrder:
    """Represents a pending sell order triggered by auto-sell logic."""
    mint: str
    symbol: str
    sell_pct: float          # 0.0 - 1.0 (fraction of remaining position)
    reason: SellReason
    triggered_at: float = field(default_factory=time)
    executed: bool = False
    result: Optional[TradeResult] = None


@dataclass
class PositionSnapshot:
    """Serializable snapshot of a position for alerts/logging."""
    mint: str
    symbol: str
    name: str
    creator: str
    entry_price_sol: float
    current_price_sol: float
    highest_price_sol: float
    pnl_pct: float
    pnl_sol: float
    confidence: str
    composite_score: float
    age_seconds: float
    sell_reason: Optional[str] = None
    exit_tx: Optional[str] = None
    exit_amount_sol: float = 0.0

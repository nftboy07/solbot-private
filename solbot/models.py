"""Data models for Solbot."""

from dataclasses import dataclass, field
from enum import Enum
from time import time
from typing import Optional


class TokenStatus(Enum):
    NEW = "new"
    QUALIFIED = "qualified"
    BUYING = "buying"
    HOLDING = "holding"
    SELLING = "selling"
    SOLD = "sold"
    REJECTED = "rejected"


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
    tx_signature: Optional[str] = None
    amount_in: float = 0.0
    amount_out: float = 0.0
    error: Optional[str] = None
    latency_ms: float = 0.0

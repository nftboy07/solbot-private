import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from enum import Enum
from dotenv import load_dotenv

load_dotenv()

class WalletTier(Enum):
    PRIORITY_A = "A"
    PRIORITY_B = "B"
    PRIORITY_C = "C"

@dataclass(frozen=True)
class ConfigManager:
    # Core RPC and Infrastructure
    rpc_endpoint: str = field(default_factory=lambda: os.getenv("SOLANA_RPC_URL", ""))
    ws_endpoint: str = field(default_factory=lambda: os.getenv("SOLANA_WS_URL", ""))
    bedrock_token: str = field(default_factory=lambda: os.getenv("AWS_BEARER_TOKEN_BEDROCK", ""))
    proxy_url: str = field(default_factory=lambda: os.getenv("PROXY_URL", ""))
    
    # Wallet Settings
    private_key: str = field(default_factory=lambda: os.getenv("WALLET_PRIVATE_KEY", ""))
    wallet_tiers: Dict[str, WalletTier] = field(default_factory=lambda: {
        "tier_a": WalletTier.PRIORITY_A,
        "tier_b": WalletTier.PRIORITY_B,
        "tier_c": WalletTier.PRIORITY_C,
    })

    # Trading Parameters
    buy_amount_sol: float = field(default_factory=lambda: float(os.getenv("BUY_AMOUNT_SOL", "0.1")))
    max_slippage_bps: int = field(default_factory=lambda: int(os.getenv("SLIPPAGE_BPS", "100")))
    
    @property
    def is_configured(self) -> bool:
        return bool(self.rpc_endpoint and self.private_key)

config = ConfigManager()

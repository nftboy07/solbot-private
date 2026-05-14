"""Configuration management for Solbot."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class SolanaConfig:
    rpc_url: str = field(default_factory=lambda: os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"))
    ws_url: str = field(default_factory=lambda: os.getenv("SOLANA_WS_URL", "wss://api.mainnet-beta.solana.com"))
    private_key: str = field(default_factory=lambda: os.getenv("WALLET_PRIVATE_KEY", ""))


@dataclass(frozen=True)
class PumpFunConfig:
    ws_url: str = field(default_factory=lambda: os.getenv("PUMPFUN_WS_URL", "wss://pumpportal.fun/api/data"))
    min_liquidity_sol: float = field(default_factory=lambda: float(os.getenv("MIN_LIQUIDITY_SOL", "5.0")))
    min_market_cap_usd: float = field(default_factory=lambda: float(os.getenv("MIN_MARKET_CAP_USD", "10000")))
    max_token_age_seconds: int = field(default_factory=lambda: int(os.getenv("MAX_TOKEN_AGE_SECONDS", "60")))


@dataclass(frozen=True)
class JupiterConfig:
    api_url: str = field(default_factory=lambda: os.getenv("JUPITER_API_URL", "https://quote-api.jup.ag/v6"))
    buy_amount_sol: float = field(default_factory=lambda: float(os.getenv("BUY_AMOUNT_SOL", "0.1")))
    slippage_bps: int = field(default_factory=lambda: int(os.getenv("SLIPPAGE_BPS", "300")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")))
    retry_delay_ms: int = field(default_factory=lambda: int(os.getenv("RETRY_DELAY_MS", "500")))


@dataclass(frozen=True)
class LogConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file: str = field(default_factory=lambda: os.getenv("LOG_FILE", "solbot.log"))


@dataclass(frozen=True)
class BotConfig:
    solana: SolanaConfig = field(default_factory=SolanaConfig)
    pumpfun: PumpFunConfig = field(default_factory=PumpFunConfig)
    jupiter: JupiterConfig = field(default_factory=JupiterConfig)
    logging: LogConfig = field(default_factory=LogConfig)

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []
        if not self.solana.private_key:
            errors.append("WALLET_PRIVATE_KEY is required")
        if self.jupiter.slippage_bps < 0 or self.jupiter.slippage_bps > 10000:
            errors.append("SLIPPAGE_BPS must be between 0 and 10000")
        if self.jupiter.buy_amount_sol <= 0:
            errors.append("BUY_AMOUNT_SOL must be positive")
        return errors

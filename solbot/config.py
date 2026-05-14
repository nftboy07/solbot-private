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
    rpc_url: str = field(default_factory=lambda: os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"))
    buy_amount_sol: float = field(default_factory=lambda: float(os.getenv("BUY_AMOUNT_SOL", "0.1")))
    slippage_bps: int = field(default_factory=lambda: int(os.getenv("SLIPPAGE_BPS", "300")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")))
    retry_delay_ms: int = field(default_factory=lambda: int(os.getenv("RETRY_DELAY_MS", "500")))
    paper_trade: bool = field(default_factory=lambda: os.getenv("PAPER_TRADE", "true").lower() in ("true", "1", "yes"))


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = field(default_factory=lambda: os.getenv("TELEGRAM_ENABLED", "false").lower() in ("true", "1", "yes"))
    bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    max_messages_per_second: int = field(default_factory=lambda: int(os.getenv("TELEGRAM_RATE_LIMIT", "3")))
    alert_on_qualified: bool = field(default_factory=lambda: os.getenv("TELEGRAM_ALERT_QUALIFIED", "true").lower() in ("true", "1", "yes"))
    alert_on_trade: bool = field(default_factory=lambda: os.getenv("TELEGRAM_ALERT_TRADE", "true").lower() in ("true", "1", "yes"))


@dataclass(frozen=True)
class ScoringConfig:
    # Scoring weights (must sum to 1.0)
    weight_liquidity: float = field(default_factory=lambda: float(os.getenv("SCORE_WEIGHT_LIQUIDITY", "0.30")))
    weight_creator: float = field(default_factory=lambda: float(os.getenv("SCORE_WEIGHT_CREATOR", "0.20")))
    weight_buy_pressure: float = field(default_factory=lambda: float(os.getenv("SCORE_WEIGHT_BUY_PRESSURE", "0.25")))
    weight_anti_rug: float = field(default_factory=lambda: float(os.getenv("SCORE_WEIGHT_ANTI_RUG", "0.25")))

    # Confidence thresholds
    high_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("HIGH_CONFIDENCE_THRESHOLD", "70.0")))
    medium_confidence_threshold: float = field(default_factory=lambda: float(os.getenv("MEDIUM_CONFIDENCE_THRESHOLD", "45.0")))

    # Only trade tokens with this minimum confidence
    min_trade_confidence: str = field(default_factory=lambda: os.getenv("MIN_TRADE_CONFIDENCE", "MEDIUM"))


@dataclass(frozen=True)
class LogConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file: str = field(default_factory=lambda: os.getenv("LOG_FILE", "solbot.log"))


@dataclass(frozen=True)
class BotConfig:
    solana: SolanaConfig = field(default_factory=SolanaConfig)
    pumpfun: PumpFunConfig = field(default_factory=PumpFunConfig)
    jupiter: JupiterConfig = field(default_factory=JupiterConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    logging: LogConfig = field(default_factory=LogConfig)

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        # Wallet required for live trading
        if not self.jupiter.paper_trade and not self.solana.private_key:
            errors.append("WALLET_PRIVATE_KEY is required for live trading")

        if self.jupiter.slippage_bps < 0 or self.jupiter.slippage_bps > 10000:
            errors.append("SLIPPAGE_BPS must be between 0 and 10000")
        if self.jupiter.buy_amount_sol <= 0:
            errors.append("BUY_AMOUNT_SOL must be positive")

        # Telegram validation
        if self.telegram.enabled:
            if not self.telegram.bot_token:
                errors.append("TELEGRAM_BOT_TOKEN required when Telegram is enabled")
            if not self.telegram.chat_id:
                errors.append("TELEGRAM_CHAT_ID required when Telegram is enabled")

        # Scoring weights should sum close to 1.0
        weight_sum = (
            self.scoring.weight_liquidity
            + self.scoring.weight_creator
            + self.scoring.weight_buy_pressure
            + self.scoring.weight_anti_rug
        )
        if abs(weight_sum - 1.0) > 0.01:
            errors.append(f"Scoring weights must sum to 1.0 (got {weight_sum:.2f})")

        # Confidence threshold ordering
        if self.scoring.medium_confidence_threshold >= self.scoring.high_confidence_threshold:
            errors.append("HIGH_CONFIDENCE_THRESHOLD must be greater than MEDIUM_CONFIDENCE_THRESHOLD")

        return errors

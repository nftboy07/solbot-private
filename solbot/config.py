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
    alert_on_sell: bool = field(default_factory=lambda: os.getenv("TELEGRAM_ALERT_SELL", "true").lower() in ("true", "1", "yes"))
    alert_on_blacklist: bool = field(default_factory=lambda: os.getenv("TELEGRAM_ALERT_BLACKLIST", "true").lower() in ("true", "1", "yes"))


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
    min_trade_confidence: str = field(default_factory=lambda: os.getenv("MIN_TRADE_CONFIDENCE", "HIGH"))


@dataclass(frozen=True)
class TradingConfig:
    """Auto-buy and auto-sell trading parameters."""

    # ── Stop Loss ───────────────────────────────────────────────────────
    stop_loss_pct: float = field(default_factory=lambda: float(os.getenv("STOP_LOSS_PCT", "30.0")))

    # ── Take Profit Tiers ───────────────────────────────────────────────
    tp1_multiplier: float = field(default_factory=lambda: float(os.getenv("TP1_MULTIPLIER", "2.0")))
    tp1_sell_pct: float = field(default_factory=lambda: float(os.getenv("TP1_SELL_PCT", "0.30")))
    tp2_multiplier: float = field(default_factory=lambda: float(os.getenv("TP2_MULTIPLIER", "3.0")))
    tp2_sell_pct: float = field(default_factory=lambda: float(os.getenv("TP2_SELL_PCT", "0.30")))
    tp3_multiplier: float = field(default_factory=lambda: float(os.getenv("TP3_MULTIPLIER", "5.0")))
    tp3_sell_pct: float = field(default_factory=lambda: float(os.getenv("TP3_SELL_PCT", "0.40")))

    # ── Trailing Stop ───────────────────────────────────────────────────
    trailing_stop_pct: float = field(default_factory=lambda: float(os.getenv("TRAILING_STOP_PCT", "20.0")))
    trailing_stop_activation_pct: float = field(default_factory=lambda: float(os.getenv("TRAILING_STOP_ACTIVATION_PCT", "50.0")))

    # ── Position Limits ─────────────────────────────────────────────────
    max_concurrent_positions: int = field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_POSITIONS", "5")))
    buy_cooldown_seconds: float = field(default_factory=lambda: float(os.getenv("BUY_COOLDOWN_SECONDS", "10.0")))

    # ── Price Monitoring ────────────────────────────────────────────────
    price_check_interval_seconds: float = field(default_factory=lambda: float(os.getenv("PRICE_CHECK_INTERVAL", "5.0")))

    # ── Blacklist ───────────────────────────────────────────────────────
    auto_blacklist_enabled: bool = field(default_factory=lambda: os.getenv("AUTO_BLACKLIST_ENABLED", "true").lower() in ("true", "1", "yes"))
    blacklist_on_stop_loss: bool = field(default_factory=lambda: os.getenv("BLACKLIST_ON_STOP_LOSS", "true").lower() in ("true", "1", "yes"))

    # ── Kill Switch ─────────────────────────────────────────────────────
    kill_switch_enabled: bool = field(default_factory=lambda: os.getenv("KILL_SWITCH_ENABLED", "true").lower() in ("true", "1", "yes"))
    kill_switch_max_loss_sol: float = field(default_factory=lambda: float(os.getenv("KILL_SWITCH_MAX_LOSS_SOL", "1.0")))
    kill_switch_max_consecutive_losses: int = field(default_factory=lambda: int(os.getenv("KILL_SWITCH_MAX_CONSECUTIVE_LOSSES", "5")))

    # ── Database ────────────────────────────────────────────────────────
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "solbot_data.db"))


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
    trading: TradingConfig = field(default_factory=TradingConfig)
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

        # Trading validation
        if self.trading.stop_loss_pct <= 0 or self.trading.stop_loss_pct > 100:
            errors.append("STOP_LOSS_PCT must be between 0 and 100")
        if self.trading.max_concurrent_positions < 1:
            errors.append("MAX_CONCURRENT_POSITIONS must be >= 1")
        if self.trading.buy_cooldown_seconds < 0:
            errors.append("BUY_COOLDOWN_SECONDS must be >= 0")

        # TP tier ordering
        if not (self.trading.tp1_multiplier < self.trading.tp2_multiplier < self.trading.tp3_multiplier):
            errors.append("Take profit multipliers must be in ascending order (TP1 < TP2 < TP3)")

        # TP sell percentages should sum to ~1.0
        tp_sum = self.trading.tp1_sell_pct + self.trading.tp2_sell_pct + self.trading.tp3_sell_pct
        if abs(tp_sum - 1.0) > 0.05:
            errors.append(f"Take profit sell percentages should sum to 1.0 (got {tp_sum:.2f})")

        # Kill switch validation
        if self.trading.kill_switch_max_loss_sol <= 0:
            errors.append("KILL_SWITCH_MAX_LOSS_SOL must be positive")

        return errors

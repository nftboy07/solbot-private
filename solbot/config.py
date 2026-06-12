"""Configuration management for Solbot."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

from dotenv import load_dotenv

load_dotenv()


class BotMode(Enum):
    DEGEN = "degen"
    NORMAL = "normal"


@dataclass(frozen=True)
class SolanaConfig:
    rpc_url: str = field(default_factory=lambda: os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"))
    ws_url: str = field(default_factory=lambda: os.getenv("SOLANA_WS_URL", "wss://api.mainnet-beta.solana.com"))
    private_key: str = field(default_factory=lambda: os.getenv("WALLET_PRIVATE_KEY", ""))


@dataclass(frozen=True)
class PumpFunConfig:
    ws_url: str = field(default_factory=lambda: os.getenv("PUMPFUN_WS_URL", "wss://pumpportal.fun/api/data"))
    min_liquidity_sol: float = field(default_factory=lambda: float(os.getenv("MIN_LIQUIDITY_SOL", "5.0")))
    min_market_cap_usd: float = field(default_factory=lambda: float(os.getenv("MIN_MARKET_CAP_USD", "100000")))
    max_market_cap_usd: float = field(default_factory=lambda: float(os.getenv("MAX_MARKET_CAP_USD", "1000000")))
    max_token_age_seconds: int = field(default_factory=lambda: int(os.getenv("MAX_TOKEN_AGE_SECONDS", "60")))


@dataclass(frozen=True)
class JupiterConfig:
    api_url: str = field(default_factory=lambda: os.getenv("JUPITER_API_URL", "https://quote-api.jup.ag/v6"))
    buy_amount_sol: float = field(default_factory=lambda: float(os.getenv("BUY_AMOUNT_SOL", "0.005")))
    slippage_bps: int = field(default_factory=lambda: int(os.getenv("SLIPPAGE_BPS", "300")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")))
    retry_delay_ms: int = field(default_factory=lambda: int(os.getenv("RETRY_DELAY_MS", "500")))


@dataclass(frozen=True)
class StrategyConfig:
    mode: BotMode = BotMode.NORMAL
    min_confidence_score: int = 75
    tp_targets: list[dict] = field(default_factory=lambda: [
        {"multiplier": 1.3, "sell_pct": 0.25},
        {"multiplier": 1.6, "sell_pct": 0.33},
        {"multiplier": 2.0, "sell_pct": 0.50},
        {"multiplier": 3.0, "sell_pct": 1.00},
    ])
    trailing_stop_pct: float = 0.25
    stop_loss_pct: float = 0.20
    emergency_stop_loss_pct: float = 0.30
    liquidity_drop_threshold: float = 0.30
    dev_dump_score_threshold: float = -0.2
    momentum_timeout_minutes: int = 30
    mcap_tp_target_usd: float = field(default_factory=lambda: float(os.getenv("MCAP_TP_TARGET_USD", "200000")))
    max_active_positions: int = field(default_factory=lambda: int(os.getenv("MAX_ACTIVE_POSITIONS", "100")))


@dataclass(frozen=True)
class DynamicFeeConfig:
    base_fee_lamports: int = 60000
    multiplier_per_buy: float = 0.2


@dataclass(frozen=True)
class TelegramConfig:
    token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))
    api_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_API_ID", ""))
    api_hash: str = field(default_factory=lambda: os.getenv("TELEGRAM_API_HASH", ""))


@dataclass(frozen=True)
class LogConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file: str = field(default_factory=lambda: os.getenv("LOG_FILE", "solbot.log"))


@dataclass(frozen=True)
class AIConfig:
    nvidia_api_key: str = field(default_factory=lambda: os.getenv("NVIDIA_API_KEY", ""))
    nvidia_api_url: str = field(default_factory=lambda: os.getenv("NVIDIA_API_URL", "https://integrate.api.nvidia.com/v1/chat/completions"))
    nvidia_model: str = field(default_factory=lambda: os.getenv("NVIDIA_MODEL", "meta/llama-3.1-405b-instruct"))
    bluesminds_api_key: str = field(default_factory=lambda: os.getenv("BLUESMINDS_API_KEY", ""))
    minimax_api_key: str = field(default_factory=lambda: os.getenv("MINIMAX_API_KEY", ""))
    # AWS Bedrock config
    aws_bearer_token_bedrock: str = field(default_factory=lambda: os.getenv("AWS_BEARER_TOKEN_BEDROCK", ""))
    aws_region: str = field(default_factory=lambda: os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "ap-south-1")))
    bedrock_model_id: str = field(default_factory=lambda: os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-5-sonnet-20241022-v2:0"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))


@dataclass(frozen=True)
class BotConfig:
    solana: SolanaConfig = field(default_factory=SolanaConfig)
    pumpfun: PumpFunConfig = field(default_factory=PumpFunConfig)
    jupiter: JupiterConfig = field(default_factory=JupiterConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    fee: DynamicFeeConfig = field(default_factory=DynamicFeeConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    logging: LogConfig = field(default_factory=LogConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    proxy_url: str = field(default_factory=lambda: os.getenv("PROXY_URL", ""))
    proxy_list_path: str = field(default_factory=lambda: os.getenv("PROXY_LIST_PATH", "data/proxies.txt"))
    residential_proxy: str = field(default_factory=lambda: os.getenv("RESIDENTIAL_PROXY", ""))

    def validate(self) -> list[str]:
        errors = []
        if not self.solana.private_key:
            errors.append("WALLET_PRIVATE_KEY is required")
        if self.jupiter.slippage_bps < 0 or self.jupiter.slippage_bps > 10000:
            errors.append("SLIPPAGE_BPS must be between 0 and 10000")
        return errors

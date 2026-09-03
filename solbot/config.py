"""Configuration management for Solbot."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_csv(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_float_first(*names: str, default: float) -> float:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and str(raw).strip():
            return float(raw)
    return float(default)


def _env_int_first(*names: str, default: int) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and str(raw).strip():
            return int(raw)
    return int(default)


def _read_secret_from_env_or_file(env_name: str, file_env_name: str) -> str:
    direct_value = os.getenv(env_name, "").strip()
    if direct_value:
        return direct_value

    file_path = os.getenv(file_env_name, "").strip()
    if not file_path:
        return ""

    try:
        content = Path(file_path).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""

    for line in content.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if value.startswith(f"{env_name}="):
            return value.split("=", 1)[1].strip().strip('"').strip("'")
        return value
    return ""


class BotMode(Enum):
    DEGEN = "degen"
    NORMAL = "normal"


@dataclass(frozen=True)
class SolanaConfig:
    rpc_url: str = field(default_factory=lambda: os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"))
    ws_url: str = field(default_factory=lambda: os.getenv("SOLANA_WS_URL", "wss://api.mainnet-beta.solana.com"))
    # Same secret pattern as OPENAI_API_KEY: env value, or first non-comment line in a file.
    private_key: str = field(
        default_factory=lambda: _read_secret_from_env_or_file("WALLET_PRIVATE_KEY", "WALLET_PRIVATE_KEY_FILE")
    )


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
    buy_amount_sol: float = field(default_factory=lambda: _env_float_first("SNIPER_CLIP_SOL", "BUY_AMOUNT_SOL", default=0.25))
    slippage_bps: int = field(default_factory=lambda: int(os.getenv("SLIPPAGE_BPS", "300")))
    max_retries: int = field(default_factory=lambda: int(os.getenv("MAX_RETRIES", "3")))
    retry_delay_ms: int = field(default_factory=lambda: int(os.getenv("RETRY_DELAY_MS", "500")))


@dataclass(frozen=True)
class StrategyConfig:
    mode: BotMode = BotMode.NORMAL
    # Paper trading: run the full decision, entry and exit lifecycle against live
    # market prices but never sign or broadcast a transaction. Lets the strategy
    # be proven end to end before the wallet is funded.
    dry_run: bool = field(default_factory=lambda: _env_bool("DRY_RUN", False))
    dry_run_start_sol: float = field(
        default_factory=lambda: float(os.getenv("DRY_RUN_START_SOL", "5.0"))
    )
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
    max_active_positions: int = field(
        default_factory=lambda: _env_int_first("SNIPER_MAX_OPEN", "MAX_ACTIVE_POSITIONS", default=3)
    )


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
    admin_ids: tuple[int, ...] = field(default_factory=lambda: tuple(
        int(x.strip()) for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if x.strip().isdigit()
    ))


@dataclass(frozen=True)
class LogConfig:
    level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    log_file: str = field(default_factory=lambda: os.getenv("LOG_FILE", "solbot.log"))


@dataclass(frozen=True)
class AIConfig:
    openai_api_key: str = field(default_factory=lambda: _read_secret_from_env_or_file("OPENAI_API_KEY", "OPENAI_API_KEY_FILE"))
    openai_api_url: str = field(default_factory=lambda: os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/responses"))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.4-mini"))
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
    openrouter_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_api_url: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions"))
    openrouter_model: str = field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3-8b-instruct:free"))
    fail_open_score: int = field(default_factory=lambda: int(os.getenv("AI_FAIL_OPEN_SCORE", "0")))


@dataclass(frozen=True)
class ArbitrageConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("ARBITRAGE_ENABLED", False))
    dry_run: bool = field(default_factory=lambda: _env_bool("ARBITRAGE_DRY_RUN", True))
    watch_mints: list[str] = field(default_factory=lambda: _env_csv("ARBITRAGE_WATCH_MINTS"))
    route_dexes: list[str] = field(default_factory=lambda: _env_csv("ARBITRAGE_ROUTE_DEXES", "Raydium,Meteora,Orca,Pump.fun"))
    input_sol: float = field(default_factory=lambda: float(os.getenv("ARBITRAGE_INPUT_SOL", "0.10")))
    min_profit_sol: float = field(default_factory=lambda: float(os.getenv("ARBITRAGE_MIN_PROFIT_SOL", "0.02")))
    estimated_fees_sol: float = field(default_factory=lambda: float(os.getenv("ARBITRAGE_ESTIMATED_FEES_SOL", "0.003")))
    jito_tip_sol: float = field(default_factory=lambda: float(os.getenv("ARBITRAGE_JITO_TIP_SOL", "0.001")))
    scan_interval_seconds: float = field(default_factory=lambda: float(os.getenv("ARBITRAGE_SCAN_INTERVAL_SECONDS", "15")))
    slippage_bps: int = field(default_factory=lambda: int(os.getenv("ARBITRAGE_SLIPPAGE_BPS", "100")))
    quote_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("ARBITRAGE_QUOTE_TIMEOUT_SECONDS", "6")))
    log_file: str = field(default_factory=lambda: os.getenv("ARBITRAGE_LOG_FILE", "arbitrage.log"))


@dataclass(frozen=True)
class CabalConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("CABAL_DETECTOR_ENABLED", True))
    top_holders_limit: int = field(default_factory=lambda: int(os.getenv("CABAL_TOP_HOLDERS_LIMIT", "20")))
    max_cluster_supply_pct: float = field(default_factory=lambda: float(os.getenv("CABAL_MAX_CLUSTER_SUPPLY_PCT", "30")))
    max_trace_hops: int = field(default_factory=lambda: int(os.getenv("CABAL_MAX_TRACE_HOPS", "3")))
    cache_ttl_seconds: float = field(default_factory=lambda: float(os.getenv("CABAL_CACHE_TTL_SECONDS", "180")))
    rpc_timeout_seconds: float = field(default_factory=lambda: float(os.getenv("CABAL_RPC_TIMEOUT_SECONDS", "8")))


@dataclass(frozen=True)
class BrainConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("BRAIN_ML_ENABLED", True))
    model_path: str = field(default_factory=lambda: os.getenv("BRAIN_MODEL_PATH", "data/risk_model.pkl"))
    scaler_path: str = field(default_factory=lambda: os.getenv("BRAIN_SCALER_PATH", "data/scaler.pkl"))
    min_score_normal: int = field(default_factory=lambda: int(os.getenv("BRAIN_MIN_SCORE_NORMAL", "60")))
    min_samples_for_training: int = field(default_factory=lambda: int(os.getenv("BRAIN_MIN_SAMPLES_FOR_TRAINING", "5")))
    autotune_interval_seconds: int = field(default_factory=lambda: int(os.getenv("BRAIN_AUTOTUNE_INTERVAL_SECONDS", "3600")))


@dataclass(frozen=True)
class PasteTradeConfig:
    api_key: str = field(default_factory=lambda: os.getenv("PASTE_TRADE_KEY", os.getenv("PASTE_TRADE_API_KEY", "")))
    api_url: str = field(default_factory=lambda: os.getenv("PASTE_TRADE_URL", os.getenv("BOARD_URL", "https://paste.trade")))
    handle: str = field(default_factory=lambda: os.getenv("PASTE_TRADE_HANDLE", "@solbot"))


def _optional_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return float(raw)


@dataclass(frozen=True)
class SniperConfig:
    """Meme-token sniper knobs. Env wins; never hardcode bankroll in the loop."""

    enabled: bool = field(default_factory=lambda: _env_bool("SNIPER_ENABLED", True))
    bankroll_sol: float = field(default_factory=lambda: float(os.getenv("SNIPER_BANKROLL_SOL", "1.3")))
    clip_sol: float = field(
        default_factory=lambda: _env_float_first("SNIPER_CLIP_SOL", "BUY_AMOUNT_SOL", default=0.25)
    )
    max_open: int = field(
        default_factory=lambda: _env_int_first("SNIPER_MAX_OPEN", "MAX_ACTIVE_POSITIONS", default=3)
    )
    fee_reserve_sol: float = field(default_factory=lambda: float(os.getenv("MIN_WALLET_SOL_RESERVE", "0.1")))
    scan_interval_seconds: float = field(
        default_factory=lambda: float(os.getenv("SNIPER_SCAN_INTERVAL_SECONDS", "1.0"))
    )
    delay_seconds: float | None = field(default_factory=lambda: _optional_float("SNIPER_DELAY_SECONDS"))
    sources: list[str] = field(
        default_factory=lambda: [s.lower() for s in _env_csv("SNIPER_SOURCES", "pumpfun,raydium")]
    )

    def rules(self):
        from solbot.sniper_bankroll import BankrollRules

        return BankrollRules(
            bankroll_sol=self.bankroll_sol,
            clip_sol=self.clip_sol,
            max_open=self.max_open,
            fee_reserve_sol=self.fee_reserve_sol,
        )


@dataclass(frozen=True)
class HummingbotConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("HUMMINGBOT_ENABLED", False))
    gateway_url: str = field(default_factory=lambda: os.getenv("HUMMINGBOT_GATEWAY_URL", "http://127.0.0.1:15888"))
    network: str = field(default_factory=lambda: os.getenv("HUMMINGBOT_NETWORK", "mainnet-beta"))
    cert_path: str = field(default_factory=lambda: os.getenv("HUMMINGBOT_CERT_PATH", ""))
    key_path: str = field(default_factory=lambda: os.getenv("HUMMINGBOT_KEY_PATH", ""))
    passphrase: str = field(default_factory=lambda: os.getenv("HUMMINGBOT_PASSPHRASE", ""))
    timeout_seconds: float = field(default_factory=lambda: float(os.getenv("HUMMINGBOT_TIMEOUT_SECONDS", "10.0")))
    pmm_default_spread_bps: int = field(default_factory=lambda: int(os.getenv("HUMMINGBOT_PMM_SPREAD_BPS", "150")))
    pmm_order_refresh_seconds: float = field(default_factory=lambda: float(os.getenv("HUMMINGBOT_PMM_REFRESH_SECONDS", "15.0")))
    pmm_max_inventory_sol: float = field(default_factory=lambda: float(os.getenv("HUMMINGBOT_PMM_MAX_INVENTORY_SOL", "0.5")))


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
    arbitrage: ArbitrageConfig = field(default_factory=ArbitrageConfig)
    cabal: CabalConfig = field(default_factory=CabalConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    paste_trade: PasteTradeConfig = field(default_factory=PasteTradeConfig)
    hummingbot: HummingbotConfig = field(default_factory=HummingbotConfig)
    sniper: SniperConfig = field(default_factory=SniperConfig)
    proxy_url: str = field(default_factory=lambda: os.getenv("PROXY_URL", ""))
    proxy_list_path: str = field(default_factory=lambda: os.getenv("PROXY_LIST_PATH", "data/proxies.txt"))
    residential_proxy: str = field(default_factory=lambda: os.getenv("RESIDENTIAL_PROXY", ""))

    def validate(self) -> list[str]:
        errors = []
        if not self.solana.private_key and not self.strategy.dry_run:
            errors.append("WALLET_PRIVATE_KEY is required")
        if self.jupiter.slippage_bps < 0 or self.jupiter.slippage_bps > 10000:
            errors.append("SLIPPAGE_BPS must be between 0 and 10000")
        if self.arbitrage.input_sol <= 0:
            errors.append("ARBITRAGE_INPUT_SOL must be positive")
        if self.arbitrage.min_profit_sol < 0:
            errors.append("ARBITRAGE_MIN_PROFIT_SOL must be non-negative")
        if self.cabal.top_holders_limit <= 0:
            errors.append("CABAL_TOP_HOLDERS_LIMIT must be positive")
        if self.cabal.max_cluster_supply_pct <= 0:
            errors.append("CABAL_MAX_CLUSTER_SUPPLY_PCT must be positive")
        if self.sniper.clip_sol <= 0:
            errors.append("SNIPER_CLIP_SOL / BUY_AMOUNT_SOL must be positive")
        if self.sniper.max_open <= 0:
            errors.append("SNIPER_MAX_OPEN / MAX_ACTIVE_POSITIONS must be positive")
        if self.sniper.scan_interval_seconds <= 0:
            errors.append("SNIPER_SCAN_INTERVAL_SECONDS must be positive")
        if self.sniper.fee_reserve_sol < 0:
            errors.append("MIN_WALLET_SOL_RESERVE must be non-negative")
        return errors

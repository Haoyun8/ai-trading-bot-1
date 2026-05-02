import os
from dataclasses import dataclass, field
from dotenv import load_dotenv
load_dotenv()

@dataclass
class ExchangeConfig:
    api_key: str = os.getenv("BINANCE_API_KEY", "")
    secret: str = os.getenv("BINANCE_SECRET", "")
    testnet: bool = os.getenv("BINANCE_TESTNET", "true").lower() == "true"
    symbols: list = field(default_factory=lambda: os.getenv("SYMBOLS", "BTC/USDT:USDT").split(","))
    default_leverage: int = int(os.getenv("DEFAULT_LEVERAGE", "10"))
    default_timeframe: str = os.getenv("DEFAULT_TIMEFRAME", "15m")
    higher_tf: str = "1h"
    use_mtf: bool = True

@dataclass
class AIConfig:
    openrouter_key: str = os.getenv("OPENROUTER_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
    openrouter_model: str = os.getenv("ACTIVE_MODEL", "openai/gpt-5.2")
    openrouter_base: str = "https://openrouter.ai/api/v1"
    openai_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_model: str = "gpt-4o"
    anthropic_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    anthropic_model: str = "claude-3-5-sonnet-20241022"
    google_key: str = os.getenv("GOOGLE_API_KEY", "")
    google_model: str = "gemini-1.5-pro"
    active_model: str = "openrouter"

@dataclass
class RiskConfig:
    max_positions: int = int(os.getenv("MAX_POSITIONS", "5"))
    max_daily_loss: float = float(os.getenv("MAX_DAILY_LOSS", "5000"))
    max_drawdown_pct: float = float(os.getenv("MAX_DRAWDOWN_PCT", "10"))
    max_position_value: float = 50000.0
    max_leverage: int = 50
    min_stop_distance_pct: float = 0.002
    trailing_stop_activation: float = 0.01
    trailing_stop_distance: float = 0.005
    cooldown_seconds: int = 1800
    max_consecutive_losses: int = 3
    risk_per_trade: float = 0.02
    use_kelly: bool = True
    kelly_fraction: float = 0.3
    limit_order_timeout: int = 120
    max_slippage_pct: float = 0.001
    max_funding_rate: float = 0.001
    position_cache_ttl: int = 10  # seconds, reduce Binance API calls
    api_rate_limit_retries: int = 3
    api_retry_base_delay: float = 2.0

@dataclass
class StrategyConfig:
    min_confidence: int = 60
    ai_weight: float = 0.7
    technical_weight: float = 0.3
    signal_cooldown: int = 300
    higher_tf_required: bool = True
    volume_spike_threshold: float = 2.0

@dataclass
class NotificationConfig:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    enable_telegram: bool = False
    def __post_init__(self):
        self.enable_telegram = bool(self.telegram_bot_token and self.telegram_chat_id)

@dataclass
class Config:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    notify: NotificationConfig = field(default_factory=NotificationConfig)
    host: str = "127.0.0.1"  # FIX: default to localhost, use Nginx for external
    port: int = int(os.getenv("PORT", "8000"))
    debug: bool = False
    api_key: str = os.getenv("API_KEY", "")
    db_path: str = "data/ai-trader.db"
    auto_trade: bool = os.getenv("AUTO_TRADE", "false").lower() == "true"
    ws_auth_token: str = os.getenv("WS_AUTH_TOKEN", "")  # separate WS auth token

config = Config()

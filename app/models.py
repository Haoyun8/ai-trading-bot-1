from __future__ import annotations
from pydantic import BaseModel
from typing import Optional, Literal
from dataclasses import dataclass, field
import time

class OrderRequest(BaseModel):
    symbol: str = "BTC/USDT:USDT"
    side: Literal["buy", "sell"] = "buy"
    type: Literal["market", "limit"] = "limit"
    amount: float
    price: Optional[float] = None
    leverage: int = 10
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None

class CloseRequest(BaseModel):
    symbol: str
    side: Literal["long", "short"]

class BacktestRequest(BaseModel):
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    days: int = 30

class OptimizeRequest(BaseModel):
    symbol: str = "BTC/USDT:USDT"
    timeframe: str = "15m"
    days: int = 30

class AIChatRequest(BaseModel):
    message: str
    model: Optional[str] = None

class AutoTradeToggle(BaseModel):
    enabled: bool

@dataclass
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    def to_dict(self):
        return {"time": self.timestamp, "open": self.open, "high": self.high,
                "low": self.low, "close": self.close, "volume": self.volume}

@dataclass
class Signal:
    direction: Literal["buy", "sell", "neutral"]
    symbol: str
    timeframe: str
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    reasoning: str
    indicators: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    def to_dict(self):
        return {"direction": self.direction, "symbol": self.symbol,
                "confidence": self.confidence, "entry": self.entry_price,
                "stop_loss": self.stop_loss, "take_profit": self.take_profit,
                "reasoning": self.reasoning, "indicators": self.indicators,
                "timestamp": self.timestamp}

@dataclass
class PositionInfo:
    symbol: str
    side: str
    amount: float
    entry_price: float
    mark_price: float
    leverage: int
    unrealized_pnl: float
    roe: float
    liquidation_price: float = 0.0
    def to_dict(self):
        return {"symbol": self.symbol, "pair": self.symbol.replace(":USDT", "").replace("/USDT", "/USDT"),
                "side": self.side, "amount": self.amount, "entry": self.entry_price,
                "mark": self.mark_price, "leverage": self.leverage,
                "pnl": round(self.unrealized_pnl, 2), "roe": round(self.roe, 2),
                "liquidation": self.liquidation_price}

@dataclass
class BacktestResult:
    total_return_pct: float
    win_rate: float
    sharpe_ratio: float
    max_drawdown_pct: float
    profit_factor: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win: float
    avg_loss: float
    equity_curve: list = field(default_factory=list)
    def to_dict(self):
        return {"total_return": f"{self.total_return_pct:+.1f}%",
                "win_rate": f"{self.win_rate:.1f}%",
                "sharpe": f"{self.sharpe_ratio:.2f}",
                "max_drawdown": f"-{self.max_drawdown_pct:.1f}%",
                "profit_factor": f"{self.profit_factor:.2f}",
                "total_trades": self.total_trades,
                "equity_curve": self.equity_curve}

@dataclass
class OrderInfo:
    id: str
    symbol: str
    side: str
    type: str
    amount: float
    price: float
    status: str
    timestamp: float

@dataclass
class TradeRecord:
    id: int
    timestamp: float
    symbol: str
    side: str
    amount: float
    entry: float
    exit_price: float
    pnl: float
    fee: float
    status: str
    def to_dict(self):
        return {"id": self.id, "timestamp": self.timestamp, "symbol": self.symbol,
                "side": self.side, "amount": self.amount, "entry": self.entry,
                "exit": self.exit_price, "pnl": round(self.pnl, 2),
                "fee": round(self.fee, 4), "status": self.status}

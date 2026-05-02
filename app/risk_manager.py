import logging, time, math
from datetime import date
from app.config import config
from app.models import Signal
from app.database import save_trade, save_risk_state, load_risk_state

log = logging.getLogger("risk")


class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.peak_equity = 0.0
        self.current_equity = 0.0
        self._last_date = date.today()
        self.consecutive_losses = 0
        self.last_loss_time = 0.0
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.gross_profit = 0.0
        self.gross_loss = 0.0
        self._initialized = False
        # --- Signal accuracy tracking ---
        self.signal_outcomes = []  # list of {"direction": str, "result": float, "confidence": float}
        self._recent_returns = []  # rolling window for volatility calc

    async def initialize(self):
        state = await load_risk_state()
        today = date.today().isoformat()
        if state.get("last_date") != today:
            self.daily_pnl = 0.0
            self.consecutive_losses = 0
            self.last_loss_time = 0.0
        else:
            self.daily_pnl = state["daily_pnl"]
            self.consecutive_losses = state["consecutive_losses"]
            self.last_loss_time = state["last_loss_time"]
        self._last_date = date.today()

    def update_equity(self, e):
        self.current_equity = e
        if not self._initialized or e > self.peak_equity:
            self.peak_equity = e
        if not self._initialized and e > 0:
            self._initialized = True
            log.info("Equity initialized: $%.2f", e)

    @property
    def drawdown_pct(self):
        if self.peak_equity > 0:
            return (self.peak_equity - self.current_equity) / self.peak_equity * 100
        return 0

    @property
    def recent_volatility(self):
        """Annualized volatility from recent returns (for position sizing)."""
        import numpy as np
        if len(self._recent_returns) < 10:
            return 0.5  # default 50% if insufficient data
        return float(np.std(self._recent_returns) * np.sqrt(252))

    def _kelly_size(self):
        """Enhanced Kelly with volatility scaling."""
        if self.total_trades < 10:
            return config.risk.risk_per_trade
        if self.losses == 0:
            return config.risk.risk_per_trade
        win_rate = self.wins / self.total_trades
        avg_win = self.gross_profit / self.wins if self.wins > 0 else 0
        avg_loss = self.gross_loss / self.losses if self.losses > 0 else 0
        if avg_loss == 0:
            return config.risk.risk_per_trade
        b = avg_win / avg_loss
        p = win_rate
        q = 1 - p
        k = (b * p - q) / b if b != 0 else 0
        base_kelly = max(0.0, k * config.risk.kelly_fraction)

        # --- Volatility scaling: reduce size in high-vol regimes ---
        vol = self.recent_volatility
        vol_scalar = 1.0
        if vol > 0.8:      # >80% annualized vol -> halve size
            vol_scalar = 0.5
        elif vol > 0.6:    # 60-80% -> reduce 25%
            vol_scalar = 0.75
        elif vol < 0.3:    # <30% vol -> slightly increase
            vol_scalar = 1.1

        adjusted = base_kelly * vol_scalar
        log.debug("Kelly: base=%.3f, vol=%.2f, scalar=%.2f, final=%.3f",
                  base_kelly, vol, vol_scalar, adjusted)
        return adjusted

    def check_correlation(self, signal, positions):
        """Prevent opening correlated positions in same direction.
        
        BTC and ETH are ~0.85 correlated. If we already have a BTC long,
        opening ETH long effectively doubles exposure without diversification.
        """
        if not positions:
            return {"ok": True}

        # Known high-correlation pairs
        CORR_GROUPS = {
            "BTC": ["BTC/USDT:USDT"],
            "ETH": ["ETH/USDT:USDT"],
            "L1": ["SOL/USDT:USDT", "AVAX/USDT:USDT", "DOT/USDT:USDT",
                   "ADA/USDT:USDT", "NEAR/USDT:USDT"],
            "MEME": ["DOGE/USDT:USDT", "SHIB/USDT:USDT", "PEPE/USDT:USDT"],
        }

        # Find which group this signal belongs to
        signal_group = None
        for group, symbols in CORR_GROUPS.items():
            if signal.symbol in symbols:
                signal_group = group
                break

        if signal_group is None:
            return {"ok": True}  # Unknown symbol, allow

        # Count existing positions in same group + same direction
        same_dir_count = 0
        for pos in positions:
            pos_group = None
            for group, symbols in CORR_GROUPS.items():
                if pos.symbol in symbols:
                    pos_group = group
                    break
            if pos_group == signal_group:
                pos_side = "buy" if pos.side == "long" else "sell"
                if pos_side == signal.direction:
                    same_dir_count += 1

        if same_dir_count >= 2:
            return {
                "ok": False,
                "reason": f"Correlation limit: {same_dir_count} {signal_group} positions in same direction"
            }

        # Also limit total exposure across all correlated groups
        total_same_dir = sum(1 for p in positions
                             if (p.side == "long" and signal.direction == "buy") or
                                (p.side == "short" and signal.direction == "sell"))
        if total_same_dir >= 3:
            return {
                "ok": False,
                "reason": f"Total directional exposure limit: {total_same_dir} positions already {signal.direction}"
            }

        return {"ok": True}

    def update_trailing_stop(self, symbol, side, entry_price, current_price, atr=None):
        """ATR-adaptive trailing stop instead of fixed percentage."""
        c = config.risk
        if side == "long":
            profit_pct = (current_price - entry_price) / entry_price
            if profit_pct >= c.trailing_stop_activation:
                # Use ATR-based distance if available, else fallback to fixed
                if atr and atr > 0:
                    # Trail at 1.5x ATR below current price
                    trail_distance = atr * 1.5 / current_price
                    trail_distance = max(trail_distance, 0.003)  # min 0.3%
                    trail_distance = min(trail_distance, 0.015)  # max 1.5%
                else:
                    trail_distance = c.trailing_stop_distance
                new_sl = current_price * (1 - trail_distance)
                return max(new_sl, entry_price * 1.001)
        else:
            profit_pct = (entry_price - current_price) / entry_price
            if profit_pct >= c.trailing_stop_activation:
                if atr and atr > 0:
                    trail_distance = atr * 1.5 / current_price
                    trail_distance = max(trail_distance, 0.003)
                    trail_distance = min(trail_distance, 0.015)
                else:
                    trail_distance = c.trailing_stop_distance
                new_sl = current_price * (1 + trail_distance)
                return min(new_sl, entry_price * 0.999)
        return None

    def check_signal(self, signal: Signal, positions: list):
        c = config.risk
        if self.consecutive_losses >= c.max_consecutive_losses:
            return {"allowed": False, "reason": f"Circuit breaker: {self.consecutive_losses} consecutive losses"}
        if self.last_loss_time and time.time() - self.last_loss_time < c.cooldown_seconds:
            return {"allowed": False, "reason": "Loss cooldown active"}
        if signal.confidence < 50:
            return {"allowed": False, "reason": "Confidence too low"}
        if self.daily_pnl <= -c.max_daily_loss:
            return {"allowed": False, "reason": "Daily loss limit reached"}
        if self.drawdown_pct >= c.max_drawdown_pct:
            return {"allowed": False, "reason": "Max drawdown exceeded"}
        if len(positions) >= c.max_positions:
            return {"allowed": False, "reason": "Max positions reached"}
        if signal.direction == "neutral":
            return {"allowed": False, "reason": "No direction"}

        # --- Correlation check ---
        corr_check = self.check_correlation(signal, positions)
        if not corr_check["ok"]:
            return {"allowed": False, "reason": corr_check["reason"]}

        risk_pct = self._kelly_size()
        risk_capital = self.current_equity * risk_pct
        raw_ru = abs(signal.entry_price - signal.stop_loss)
        min_ru = signal.entry_price * c.min_stop_distance_pct
        ru = max(raw_ru, min_ru)
        amt = risk_capital / ru
        if amt * signal.entry_price > c.max_position_value:
            amt = c.max_position_value / signal.entry_price

        # --- Confidence calibration: reduce size for low-confidence signals ---
        conf_scalar = 1.0
        if signal.confidence < 70:
            conf_scalar = 0.7
        elif signal.confidence < 80:
            conf_scalar = 0.85
        amt *= conf_scalar

        return {
            "allowed": True, "reason": "Passed",
            "adjusted_amount": round(amt, 6), "risk_pct": risk_pct,
            "volatility": round(self.recent_volatility, 4),
            "conf_scalar": conf_scalar
        }

    async def record_trade(self, pnl, fee=0):
        self.daily_pnl += pnl
        self.total_trades += 1
        self._recent_returns.append(pnl / max(self.current_equity, 1))
        if len(self._recent_returns) > 100:
            self._recent_returns = self._recent_returns[-100:]
        if pnl > 0:
            self.wins += 1
            self.gross_profit += pnl
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.gross_loss += abs(pnl)
            self.consecutive_losses += 1
            self.last_loss_time = time.time()
        self.update_equity(self.current_equity + pnl - fee)
        await save_risk_state(
            self.daily_pnl, self.consecutive_losses,
            self.last_loss_time, date.today().isoformat()
        )

    def get_status(self):
        return {
            "daily_pnl": round(self.daily_pnl, 2),
            "drawdown_pct": round(self.drawdown_pct, 2),
            "current_equity": round(self.current_equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "consecutive_losses": self.consecutive_losses,
            "total_trades": self.total_trades,
            "win_rate": round(self.wins / self.total_trades * 100, 1) if self.total_trades > 0 else 0,
            "volatility": round(self.recent_volatility, 4),
        }


risk_manager = RiskManager()

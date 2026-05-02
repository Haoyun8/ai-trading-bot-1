import numpy as np, logging, time
from app.models import Candle, BacktestResult
from app.indicators import candles_to_df, calc_all

log = logging.getLogger("backtester")


class Backtester:
    """Production-grade backtester with slippage, funding costs, liquidation, walk-forward."""

    # Realistic execution costs
    MAKER_FEE = 0.0002       # 0.02% maker
    TAKER_FEE = 0.0005       # 0.05% taker
    SLIPPAGE_PCT = 0.0003    # 0.03% average slippage
    FUNDING_INTERVAL = 28800 # 8 hours in seconds
    FUNDING_RATE = 0.0001    # avg 0.01% per 8h (conservative)

    def _calc_position_size(self, equity, risk_pct, entry, stop, leverage):
        """Kelly-aware position sizing with leverage cap."""
        risk_capital = equity * risk_pct
        risk_per_unit = abs(entry - stop)
        if risk_per_unit <= 0:
            risk_per_unit = entry * 0.005  # min 0.5% stop
        amount = risk_capital / risk_per_unit
        # Cap by leverage
        max_notional = equity * leverage
        max_amount = max_notional / entry
        return min(amount, max_amount)

    def _check_liquidation(self, side, entry, price, leverage):
        """Check if position would be liquidated (simplified)."""
        # Maintenance margin ~0.5% for most futures
        maint_margin = 0.005
        if side == "long":
            liq_price = entry * (1 - (1 / leverage) + maint_margin)
            return price <= liq_price
        else:
            liq_price = entry * (1 + (1 / leverage) - maint_margin)
            return price >= liq_price

    def _apply_slippage(self, price, side, is_entry=True):
        """Apply realistic slippage to entry/exit prices."""
        direction = 1 if (side == "long" and is_entry) or (side == "short" and not is_entry) else -1
        return price * (1 + direction * self.SLIPPAGE_PCT)

    def _calc_funding_cost(self, entry, amount, bars_held, timeframe_minutes=15):
        """Calculate accumulated funding rate costs."""
        total_minutes = bars_held * timeframe_minutes
        funding_periods = (total_minutes * 60) / self.FUNDING_INTERVAL
        notional = entry * amount
        return notional * self.FUNDING_RATE * funding_periods

    def run(self, candles, params=None, leverage=10, risk_pct=0.02):
        """Run backtest with realistic execution simulation."""
        if params is None:
            params = {"rsi_oversold": 30, "rsi_overbought": 70, "atr_sl": 1.5, "atr_tp": 3.0}
        df = candles_to_df(candles)
        if len(df) < 60:
            return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        calc_all(df)
        rs = df["rsi"].values
        ms = df["macd_hist"].values
        em = df.get("ema21", df["close"].rolling(21).mean()).values
        at = df["atr"].values
        cl = df["close"].values

        trades = []
        pos = None
        eq = 10000.0
        peak_eq = eq
        max_dd = 0
        curve = [eq]
        bars_in_trade = 0

        for i in range(50, len(df)):
            p = cl[i]
            a = at[i]
            ri = rs[i]
            mh = ms[i] if not np.isnan(ms[i]) else 0

            if np.isnan(a) or a <= 0:
                continue

            if pos:
                bars_in_trade += 1

                # --- Liquidation check ---
                if self._check_liquidation(pos["s"], pos["e"], p, leverage):
                    # Liquidated - lose entire margin
                    loss = pos["margin"]
                    eq -= loss
                    trades.append({"pnl": -loss, "w": False, "liquidated": True})
                    curve.append(eq)
                    peak_eq = max(peak_eq, eq)
                    dd = (peak_eq - eq) / peak_eq * 100 if peak_eq > 0 else 0
                    max_dd = max(max_dd, dd)
                    pos = None
                    continue

                # --- Trailing stop activation ---
                if pos["s"] == "long":
                    # Activate trailing after 1% profit
                    if p >= pos["e"] * 1.01:
                        new_trail = p * 0.995
                        pos["sl"] = max(pos["sl"], new_trail)
                    hit_sl = p <= pos["sl"]
                    hit_tp = p >= pos["tp"]
                else:
                    if p <= pos["e"] * 0.99:
                        new_trail = p * 1.005
                        pos["sl"] = min(pos["sl"], new_trail)
                    hit_sl = p >= pos["sl"]
                    hit_tp = p <= pos["tp"]

                if hit_sl or hit_tp:
                    # Apply slippage on exit
                    exit_p = self._apply_slippage(
                        pos["sl"] if hit_sl else pos["tp"],
                        pos["s"], is_entry=False
                    )
                    # Calculate PnL with notional
                    if pos["s"] == "long":
                        raw_pnl = (exit_p - pos["e"]) * pos["amt"]
                    else:
                        raw_pnl = (pos["e"] - exit_p) * pos["amt"]

                    # Deduct fees (taker for market exit)
                    exit_fee = abs(pos["amt"] * exit_p) * self.TAKER_FEE
                    # Deduct funding costs
                    funding = self._calc_funding_cost(pos["e"], pos["amt"], bars_in_trade)
                    net_pnl = raw_pnl - exit_fee - funding

                    eq += net_pnl
                    peak_eq = max(peak_eq, eq)
                    dd = (peak_eq - eq) / peak_eq * 100 if peak_eq > 0 else 0
                    max_dd = max(max_dd, dd)
                    trades.append({
                        "pnl": net_pnl, "w": net_pnl > 0,
                        "funding": funding, "fee": exit_fee,
                        "bars": bars_in_trade, "liquidated": False
                    })
                    curve.append(eq)
                    pos = None
                    bars_in_trade = 0
                    continue

            # --- Entry logic ---
            if not pos and i < len(df) - 1:
                if ri < params["rsi_oversold"] and mh > 0 and p > em[i]:
                    entry_p = self._apply_slippage(p, "long", is_entry=True)
                    sl = p - a * params["atr_sl"]
                    tp = p + a * params["atr_tp"]
                    amt = self._calc_position_size(eq, risk_pct, entry_p, sl, leverage)
                    margin = (entry_p * amt) / leverage
                    entry_fee = abs(amt * entry_p) * self.TAKER_FEE
                    eq -= entry_fee  # Pay entry fee upfront
                    pos = {"s": "long", "e": entry_p, "sl": sl, "tp": tp,
                           "amt": amt, "margin": margin}
                elif ri > params["rsi_overbought"] and mh < 0 and p < em[i]:
                    entry_p = self._apply_slippage(p, "short", is_entry=True)
                    sl = p + a * params["atr_sl"]
                    tp = p - a * params["atr_tp"]
                    amt = self._calc_position_size(eq, risk_pct, entry_p, sl, leverage)
                    margin = (entry_p * amt) / leverage
                    entry_fee = abs(amt * entry_p) * self.TAKER_FEE
                    eq -= entry_fee
                    pos = {"s": "short", "e": entry_p, "sl": sl, "tp": tp,
                           "amt": amt, "margin": margin}

        # Force close any remaining position
        if pos:
            exit_p = cl[-1]
            if pos["s"] == "long":
                raw_pnl = (exit_p - pos["e"]) * pos["amt"]
            else:
                raw_pnl = (pos["e"] - exit_p) * pos["amt"]
            exit_fee = abs(pos["amt"] * exit_p) * self.TAKER_FEE
            funding = self._calc_funding_cost(pos["e"], pos["amt"], bars_in_trade)
            eq += raw_pnl - exit_fee - funding
            trades.append({"pnl": raw_pnl - exit_fee - funding, "w": raw_pnl > exit_fee + funding,
                           "funding": funding, "fee": exit_fee, "bars": bars_in_trade, "liquidated": False})

        # --- Compute statistics ---
        t = len(trades)
        w = [x for x in trades if x["w"]]
        l = [x for x in trades if not x["w"]]
        liquidations = sum(1 for x in trades if x.get("liquidated"))
        total_funding = sum(x.get("funding", 0) for x in trades)
        total_fees = sum(x.get("fee", 0) for x in trades)
        wr = len(w) / t * 100 if t else 0
        tr = (eq - 10000) / 10000 * 100

        pk = 10000
        md = 0
        rn = 10000
        for x in trades:
            rn += x["pnl"]
            pk = max(pk, rn)
            d = (pk - rn) / pk * 100
            md = max(md, d)

        rets = [x["pnl"] / 10000 for x in trades]
        sh = (np.mean(rets) / np.std(rets) * np.sqrt(252)
              if len(rets) > 1 and np.std(rets) > 0 else 0)
        gp = sum(x["pnl"] for x in w)
        gl = sum(abs(x["pnl"]) for x in l)

        result = BacktestResult(
            round(tr, 2), round(wr, 1), round(sh, 2), round(md, 2),
            round(gp / gl if gl > 0 else 0, 2), t, len(w), len(l),
            round(np.mean([x["pnl"] for x in w]) if w else 0, 2),
            round(np.mean([abs(x["pnl"]) for x in l]) if l else 0, 2),
            curve
        )
        # Attach extra metadata for walk-forward
        result._meta = {
            "liquidations": liquidations,
            "total_funding": round(total_funding, 2),
            "total_fees": round(total_fees, 2),
            "avg_bars_held": round(np.mean([x.get("bars", 0) for x in trades]), 1) if trades else 0,
        }
        return result

    def walk_forward(self, candles, param_grid, train_ratio=0.7, n_splits=3,
                     leverage=10, risk_pct=0.02):
        """Walk-forward validation: train on past, test on future.
        
        Splits data into n_splits rolling windows. For each:
        - Optimize params on train portion
        - Validate on test portion (out-of-sample)
        Returns aggregated OOS results + per-split details.
        """
        import itertools
        total = len(candles)
        if total < 200:
            return {"splits": [], "oos_trades": 0, "oos_return": 0, "oos_sharpe": 0}

        split_size = total // n_splits
        all_oos_trades = []
        split_details = []
        keys = list(param_grid.keys())
        values = list(param_grid.values())

        for split_i in range(n_splits):
            start = split_i * split_size
            end = min(start + split_size * 2, total)  # overlap for context
            split_candles = candles[start:end]
            split_len = len(split_candles)
            train_end = int(split_len * train_ratio)

            if train_end < 60 or (split_len - train_end) < 30:
                continue

            train_candles = split_candles[:train_end]
            test_candles = split_candles[train_end:]

            # Optimize on train set
            best_sharpe = -9999
            best_params = None
            for combo in itertools.product(*values):
                params = dict(zip(keys, combo))
                result = self.run(train_candles, params, leverage, risk_pct)
                if result.sharpe_ratio > best_sharpe:
                    best_sharpe = result.sharpe_ratio
                    best_params = params

            if best_params is None:
                continue

            # Test on out-of-sample
            oos_result = self.run(test_candles, best_params, leverage, risk_pct)
            all_oos_trades.append(oos_result)

            split_details.append({
                "split": split_i + 1,
                "train_bars": train_end,
                "test_bars": len(test_candles),
                "best_params": best_params,
                "train_sharpe": round(best_sharpe, 2),
                "oos_return": oos_result.total_return_pct,
                "oos_sharpe": oos_result.sharpe_ratio,
                "oos_win_rate": oos_result.win_rate,
                "oos_trades": oos_result.total_trades,
                "oos_max_dd": oos_result.max_drawdown_pct,
            })

        # Aggregate OOS results
        total_oos_trades = sum(r.total_trades for r in all_oos_trades)
        avg_oos_return = np.mean([r.total_return_pct for r in all_oos_trades]) if all_oos_trades else 0
        avg_oos_sharpe = np.mean([r.sharpe_ratio for r in all_oos_trades]) if all_oos_trades else 0
        avg_oos_wr = np.mean([r.win_rate for r in all_oos_trades]) if all_oos_trades else 0
        worst_oos_dd = max([r.max_drawdown_pct for r in all_oos_trades]) if all_oos_trades else 0

        return {
            "splits": split_details,
            "oos_trades": total_oos_trades,
            "oos_return": round(avg_oos_return, 2),
            "oos_sharpe": round(avg_oos_sharpe, 2),
            "oos_win_rate": round(avg_oos_wr, 1),
            "oos_max_dd": round(worst_oos_dd, 2),
            "degradation": round(
                (avg_oos_sharpe / max(np.mean([s["train_sharpe"] for s in split_details]), 0.01) - 1) * 100, 1
            ) if split_details else 0,
        }


backtester = Backtester()

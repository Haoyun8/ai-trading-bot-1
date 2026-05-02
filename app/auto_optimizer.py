import asyncio, logging, time
from app.exchange import exchange
from app.models import Candle
from app.optimizer import grid_search, walk_forward_search, DEFAULT_PARAM_GRID
from app.strategy import strategy_engine
from app.database import save_strategy_params, load_strategy_params
from app.config import config
from app.notifier import send_telegram

log = logging.getLogger("auto_opt")


async def run_optimization_for_symbol(symbol, timeframe):
    """Optimize with walk-forward validation before accepting new params."""
    try:
        log.info("Starting optimization %s %s", symbol, timeframe)
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit=1000)
        candles = [Candle(int(c[0] // 1000), c[1], c[2], c[3], c[4], c[5])
                    for c in ohlcv]

        # Run walk-forward search instead of pure in-sample
        best_params, in_sample, wf_report = await asyncio.to_thread(
            walk_forward_search, candles, DEFAULT_PARAM_GRID,
            config.exchange.default_leverage, config.risk.risk_per_trade,
            0.7, 3  # 70% train, 3 splits
        )

        if best_params is None:
            log.warning("No valid params found for %s", symbol)
            return

        saved = await load_strategy_params(symbol, timeframe)
        current_sharpe = saved["sharpe"] if saved else -9999

        # Only update if: in-sample improved AND walk-forward isn't terrible
        oos_sharpe = wf_report.get("oos_sharpe", 0)
        degradation = wf_report.get("degradation", 0)
        oos_trades = wf_report.get("oos_trades", 0)

        # Reject if walk-forward shows severe overfitting (>50% degradation)
        if degradation < -50 and oos_trades > 5:
            log.warning("Rejecting params for %s: severe overfitting (degradation=%.1f%%)",
                        symbol, degradation)
            await send_telegram(
                f"\u26a0\ufe0f Optimization rejected {symbol}: "
                f"overfitting detected (degradation {degradation:.1f}%)")
            return

        # Reject if OOS has too few trades (not statistically meaningful)
        if oos_trades < 3:
            log.warning("Rejecting params for %s: insufficient OOS trades (%d)", symbol, oos_trades)
            return

        if in_sample.sharpe_ratio > current_sharpe:
            await save_strategy_params(symbol, timeframe, best_params, in_sample.sharpe_ratio)
            strategy_engine.set_params(symbol, best_params)
            log.info("Strategy updated %s: %s (Sharpe %.2f -> %.2f, OOS Sharpe %.2f, degradation %.1f%%)",
                     symbol, best_params, current_sharpe, in_sample.sharpe_ratio,
                     oos_sharpe, degradation)
            await send_telegram(
                f"\U0001f527 Strategy optimized {symbol}\n"
                f"Sharpe: {in_sample.sharpe_ratio:.2f} (OOS: {oos_sharpe:.2f})\n"
                f"Degradation: {degradation:.1f}%\n"
                f"Params: {best_params}")
        else:
            log.info("Optimization did not improve %s (%.2f <= %.2f)",
                     symbol, in_sample.sharpe_ratio, current_sharpe)
    except Exception as e:
        log.error("Optimization failed %s: %s", symbol, e)


async def daily_optimization_loop():
    while True:
        now = time.time()
        next_run = (now // 86400 + 1) * 86400
        await asyncio.sleep(max(0, next_run - now))
        for sym in config.exchange.symbols:
            await run_optimization_for_symbol(sym, config.exchange.default_timeframe)

import itertools, logging
from app.backtester import backtester

log = logging.getLogger("optimizer")


def grid_search(candles, param_grid, leverage=10, risk_pct=0.02):
    """In-sample grid search. Returns best params + result."""
    best_result = None
    best_params = None
    best_sharpe = -9999
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        result = backtester.run(candles, params, leverage, risk_pct)
        if result.sharpe_ratio > best_sharpe:
            best_sharpe = result.sharpe_ratio
            best_result = result
            best_params = params
    return best_params, best_result


def walk_forward_search(candles, param_grid, leverage=10, risk_pct=0.02,
                        train_ratio=0.7, n_splits=3):
    """Walk-forward validation: optimize on train, validate on test.
    
    Returns: (best_params, in_sample_result, walk_forward_report)
    The walk_forward_report contains out-of-sample performance metrics.
    """
    # First do full in-sample for reference
    best_params, in_sample = grid_search(candles, param_grid, leverage, risk_pct)

    # Then run walk-forward
    wf_report = backtester.walk_forward(
        candles, param_grid,
        train_ratio=train_ratio, n_splits=n_splits,
        leverage=leverage, risk_pct=risk_pct
    )

    # Log walk-forward results
    if wf_report["splits"]:
        log.info("Walk-forward: %d splits, OOS return=%.1f%%, OOS Sharpe=%.2f, degradation=%.1f%%",
                 len(wf_report["splits"]), wf_report["oos_return"],
                 wf_report["oos_sharpe"], wf_report["degradation"])
        for s in wf_report["splits"]:
            log.info("  Split %d: train_sharpe=%.2f, OOS_return=%.1f%%, OOS_sharpe=%.2f",
                     s["split"], s["train_sharpe"], s["oos_return"], s["oos_sharpe"])

    return best_params, in_sample, wf_report


DEFAULT_PARAM_GRID = {
    "rsi_oversold": [20, 25, 30],
    "rsi_overbought": [70, 75, 80],
    "atr_sl": [1.0, 1.5, 2.0],
    "atr_tp": [2.0, 3.0, 4.0]
}

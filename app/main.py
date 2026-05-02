import asyncio, json, logging, time, os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException, Depends, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
from app.config import config
from app.models import (OrderRequest, CloseRequest, AIChatRequest, BacktestRequest,
                         OptimizeRequest, Signal, Candle, AutoTradeToggle)
from app.exchange import exchange
from app.data_feed import data_feed
from app.indicators import candles_to_df, calc_all
from app.strategy import strategy_engine
from app.risk_manager import risk_manager
from app.backtester import backtester
from app.optimizer import grid_search, DEFAULT_PARAM_GRID
from app.database import get_recent_trades, get_equity_curve, load_strategy_params
from app.notifier import send_telegram
from app.order_manager import order_manager
from app.auto_optimizer import daily_optimization_loop, run_optimization_for_symbol
import logging.handlers

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-10s | %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            "logs/ai-trader.log", maxBytes=10 * 1024 * 1024, backupCount=5)
    ]
)
log = logging.getLogger("main")

# Log buffer for frontend
log_buffer = []
MAX_LOG_BUFFER = 200


class LogCapture(logging.Handler):
    def emit(self, record):
        entry = {
            "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage()
        }
        log_buffer.append(entry)
        if len(log_buffer) > MAX_LOG_BUFFER:
            log_buffer.pop(0)


log_capture = LogCapture()
log_capture.setLevel(logging.INFO)
logging.getLogger().addHandler(log_capture)

wsc: set = set()


async def verify_api_key(x_api_key: str = Header(None)):
    if config.api_key and x_api_key != config.api_key:
        raise HTTPException(status_code=403, detail="Invalid API Key")


async def broadcast(mt, d):
    if not wsc:
        return
    p = json.dumps({"type": mt, **(d if isinstance(d, dict) else {"data": d})})
    dead = set()
    for w in wsc:
        try:
            await w.send_text(p)
        except Exception:
            dead.add(w)
    wsc.difference_update(dead)



async def on_tick(d):
    await broadcast("ticker", d)


async def on_kl(d):
    # FIX: d is {"symbol": ..., "candle": {...}}, broadcast the candle data directly
    await broadcast("kline", d)


async def symbol_signal_loop(symbol):
    while True:
        try:
            sym_data = data_feed.symbols.get(symbol)
            if sym_data and len(sym_data["candles"]) >= 50:
                s = await strategy_engine.generate_signal(
                    symbol, config.exchange.default_timeframe, list(sym_data["candles"]))
                if s.direction != "neutral":
                    await broadcast("signal", s.to_dict())
                    await send_telegram(
                        f"\U0001f4ca {symbol} Signal: {s.direction.upper()} "
                        f"Confidence {s.confidence:.0f}%\n{s.reasoning}")
                    await order_manager.execute_signal(s)
        except Exception as e:
            log.error("Signal error %s: %s", symbol, e)
        await asyncio.sleep(60)


async def pos_loop():
    equity_snap_interval = 300  # Save equity every 5 min
    last_snap = 0
    while True:
        try:
            ps = await exchange.fetch_positions_cached()  # FIX: use cached positions
            b = await exchange.fetch_balance()
            risk_manager.update_equity(b["total"])
            await broadcast("positions", {
                "positions": [p.to_dict() for p in ps],
                "balance": b,
                "risk": risk_manager.get_status()
            })
            # Periodic equity snapshot
            now = time.time()
            if now - last_snap > equity_snap_interval and b["total"] > 0:
                from app.database import save_equity_snapshot
                await save_equity_snapshot(b["total"])
                last_snap = now
        except Exception as e:
            log.warning("Position update failed: %s", e)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app):
    log.info("=== AI Trader Pro v7.0 Starting ===")
    data_feed.on("ticker", on_tick)
    data_feed.on("kline", on_kl)

    await risk_manager.initialize()

    try:
        await exchange.init()
        b = await exchange.fetch_balance()
        risk_manager.update_equity(b["total"])
        log.info("Balance: $%.2f (free: $%.2f)", b["total"], b["free"])
        await send_telegram("\U0001f916 AI Trader v7.0 started successfully")
    except Exception as e:
        log.critical("Exchange init failed: %s", e)
        raise SystemExit(1) from e

    ft = asyncio.create_task(data_feed.start())
    signal_tasks = [
        asyncio.create_task(symbol_signal_loop(sym))
        for sym in config.exchange.symbols
    ]
    pt = asyncio.create_task(pos_loop())
    om = asyncio.create_task(order_manager.monitor_orders())
    ts = asyncio.create_task(order_manager.manage_trailing_stops())
    opt = asyncio.create_task(daily_optimization_loop())

    yield

    ft.cancel()
    for t in signal_tasks:
        t.cancel()
    pt.cancel()
    om.cancel()
    ts.cancel()
    opt.cancel()
    await data_feed.stop()
    await send_telegram("\U0001f6d1 AI Trader stopped")
    log.info("AI Trader stopped")


app = FastAPI(title="AI Trader Pro", lifespan=lifespan)
sd = Path(__file__).parent.parent / "static"
if sd.exists():
    app.mount("/static", StaticFiles(directory=str(sd)), name="static")


@app.get("/")
async def idx(key: str = Query("")):
    f = sd / "index.html"
    if f.exists():
        return FileResponse(str(f))
    return {"msg": "AI Trader Pro API"}


@app.websocket("/ws")
async def ws_ep(ws: WebSocket, key: str = Query(""), token: str = Query("")):
    # FIX: Support both URL key param and first-message auth; prefer header-based
    authenticated = False
    if config.api_key:
        # Check URL param (backward compat) or token param
        if key == config.api_key or token == config.api_key:
            authenticated = True
        else:
            # Auth via first message (fallback)
            await ws.accept()
            try:
                auth_msg = await asyncio.wait_for(ws.receive_text(), timeout=5)
                auth_data = json.loads(auth_msg)
                if auth_data.get("api_key") == config.api_key:
                    authenticated = True
            except Exception:
                pass
            if not authenticated:
                await ws.send_text(json.dumps({"type": "error", "message": "Authentication failed"}))
                await ws.close()
                return
    else:
        authenticated = True
        await ws.accept()

    wsc.add(ws)
    try:
        prices = {sym: data_feed.symbols[sym]["price"] for sym in data_feed.symbols}
        await ws.send_text(json.dumps({
            "type": "init",
            "prices": prices,
            "model": strategy_engine.active_model,
            "risk": risk_manager.get_status(),
            "auto_trade": config.auto_trade,
            "symbols": config.exchange.symbols
        }))

        while True:
            raw = await ws.receive_text()
            try:
                m = json.loads(raw)
                a = m.get("action")
                if a == "chat":
                    resp = await strategy_engine.chat(m["message"], m.get("model"))
                    await ws.send_text(json.dumps({"type": "ai", "message": resp}))
                elif a == "trade":
                    req = OrderRequest(**m["data"])
                    sig = Signal(
                        "buy" if req.side == "buy" else "sell",
                        req.symbol, "", 80,
                        data_feed.symbols.get(req.symbol, {}).get("price", 0),
                        0, 0, "Manual"
                    )
                    ck = risk_manager.check_signal(sig, await exchange.fetch_positions_cached())
                    if ck["allowed"]:
                        req.amount = ck["adjusted_amount"]
                        if req.type == "limit" and not req.price:
                            p = data_feed.symbols[req.symbol]["price"]
                            req.price = (p * (1 - config.risk.max_slippage_pct)
                                         if req.side == "buy"
                                         else p * (1 + config.risk.max_slippage_pct))
                        o = await exchange.place_order(req)
                        await ws.send_text(json.dumps({"type": "order", "data": o}))
                        await send_telegram(
                            f"\U0001f4c8 Manual order: {req.side} {req.amount} {req.symbol}")
                    else:
                        await ws.send_text(
                            json.dumps({"type": "error", "message": ck["reason"]}))
                elif a == "close":
                    symbol = m.get("symbol", "")
                    side = m.get("side", "")
                    result = await order_manager.manual_close(symbol, side)
                    await ws.send_text(json.dumps({"type": "close_result", "data": str(result)}))
                elif a == "switch_model":
                    strategy_engine.active_model = m.get("model", "openrouter")
                    await ws.send_text(json.dumps({
                        "type": "model_switched",
                        "model": strategy_engine.active_model
                    }))
                elif a == "get_logs":
                    limit = m.get("limit", 50)
                    await ws.send_text(json.dumps({
                        "type": "logs",
                        "data": log_buffer[-limit:]
                    }))
            except Exception as e:
                log.error("WS message error: %s", e)
    except WebSocketDisconnect:
        pass
    finally:
        wsc.discard(ws)


@app.get("/api/status")
async def st():
    return {
        "prices": {sym: data_feed.symbols[sym]["price"] for sym in data_feed.symbols},
        "model": strategy_engine.active_model,
        "risk": risk_manager.get_status(),
        "auto_trade": config.auto_trade,
        "symbols": config.exchange.symbols
    }


@app.post("/api/auto_trade", dependencies=[Depends(verify_api_key)])
async def toggle_auto(t: AutoTradeToggle):
    config.auto_trade = t.enabled
    await send_telegram(f"Auto trade {'enabled' if t.enabled else 'disabled'}")
    return {"auto_trade": config.auto_trade}


@app.get("/api/positions", dependencies=[Depends(verify_api_key)])
async def gp():
    return {"positions": [p.to_dict() for p in await exchange.fetch_positions()]}


@app.post("/api/order", dependencies=[Depends(verify_api_key)])
async def po(r: OrderRequest):
    return {"order": await exchange.place_order(r)}


@app.post("/api/close", dependencies=[Depends(verify_api_key)])
async def cl(r: CloseRequest):
    return {"result": await order_manager.manual_close(r.symbol, r.side)}


@app.get("/api/signal/{symbol}", dependencies=[Depends(verify_api_key)])
async def gs(symbol: str):
    sym_data = data_feed.symbols.get(symbol)
    if not sym_data:
        return {"error": "symbol not found"}
    s = await strategy_engine.generate_signal(
        symbol, config.exchange.default_timeframe, list(sym_data["candles"]))
    return s.to_dict()


@app.post("/api/backtest", dependencies=[Depends(verify_api_key)])
async def bt(r: BacktestRequest):
    ohlcv = await exchange.fetch_ohlcv(r.symbol, r.timeframe, 1000)
    cs = [Candle(int(c[0] // 1000), c[1], c[2], c[3], c[4], c[5]) for c in ohlcv]
    result = await asyncio.to_thread(backtester.run, cs)
    d = result.to_dict()
    # Include extra metadata (fees, funding, liquidations)
    if hasattr(result, "_meta"):
        d["execution_costs"] = result._meta
    return d


@app.post("/api/walk_forward", dependencies=[Depends(verify_api_key)])
async def wf(r: OptimizeRequest):
    """Run walk-forward validation and return OOS performance."""
    from app.optimizer import walk_forward_search, DEFAULT_PARAM_GRID
    ohlcv = await exchange.fetch_ohlcv(r.symbol, r.timeframe, 1000)
    cs = [Candle(int(c[0] // 1000), c[1], c[2], c[3], c[4], c[5]) for c in ohlcv]
    best_params, in_sample, wf_report = await asyncio.to_thread(
        walk_forward_search, cs, DEFAULT_PARAM_GRID,
        config.exchange.default_leverage, config.risk.risk_per_trade
    )
    return {
        "symbol": r.symbol,
        "best_params": best_params,
        "in_sample": in_sample.to_dict() if in_sample else None,
        "walk_forward": wf_report
    }


@app.post("/api/optimize", dependencies=[Depends(verify_api_key)])
async def opt(r: OptimizeRequest):
    await run_optimization_for_symbol(r.symbol, r.timeframe)
    saved = await load_strategy_params(r.symbol, r.timeframe)
    return {"symbol": r.symbol, "params": saved}


@app.post("/api/chat", dependencies=[Depends(verify_api_key)])
async def ch(r: AIChatRequest):
    return {"response": await strategy_engine.chat(r.message, r.model)}


@app.get("/api/risk", dependencies=[Depends(verify_api_key)])
async def rk():
    return risk_manager.get_status()


@app.get("/api/indicators/{symbol}", dependencies=[Depends(verify_api_key)])
async def ind(symbol: str):
    sym_data = data_feed.symbols.get(symbol)
    if not sym_data or len(sym_data["candles"]) < 50:
        return {"error": "Insufficient data"}
    df = await asyncio.to_thread(candles_to_df, list(sym_data["candles"]))
    return await asyncio.to_thread(calc_all, df)

@app.get("/api/candles/{symbol}", dependencies=[Depends(verify_api_key)])
async def candles(symbol: str, limit: int = 200):
    sym_data = data_feed.symbols.get(symbol)
    if not sym_data:
        return {"error": "symbol not found"}
    cs = list(sym_data["candles"])[-limit:]
    return [c.to_dict() for c in cs]


@app.get("/api/trades", dependencies=[Depends(verify_api_key)])
async def trades():
    rows = await get_recent_trades(50)
    return [dict(r) for r in rows]


@app.get("/api/equity", dependencies=[Depends(verify_api_key)])
async def equity():
    return await get_equity_curve(7)


@app.get("/api/logs", dependencies=[Depends(verify_api_key)])
async def get_logs(limit: int = 50):
    return log_buffer[-limit:]


if __name__ == "__main__":
    uvicorn.run("app.main:app", host=config.host, port=config.port, reload=config.debug)

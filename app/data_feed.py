import asyncio, json, time, logging
import websockets
from collections import deque
from app.models import Candle
from app.config import config
from app.exchange import exchange

log = logging.getLogger("data_feed")


class DataFeed:
    def __init__(self):
        self.symbols = {}
        self._ws = None
        self._running = False
        self._cbs = {"ticker": [], "kline": [], "trade": [], "depth": []}
        self._reconnect_delay = 1
        self._max_reconnect_delay = 60

    def on(self, event, cb):
        if event in self._cbs:
            self._cbs[event].append(cb)

    def _emit(self, event, data):
        for cb in self._cbs.get(event, []):
            try:
                r = cb(data)
                if asyncio.iscoroutine(r):
                    asyncio.create_task(r)
            except Exception as e:
                log.error("Callback error: %s", e)

    @property
    def ws_url(self):
        streams = []
        for sym in config.exchange.symbols:
            s = sym.lower().replace("/", "").replace(":usdt", "")
            streams.append(
                f"{s}@ticker/{s}@kline_{config.exchange.default_timeframe}"
                f"/{s}@trade/{s}@depth20@100ms"
            )
        base = "wss://dstream.binancefuture.com/stream"
        return f"{base}/{','.join(streams)}"

    async def start(self):
        for sym in config.exchange.symbols:
            self.symbols[sym] = {"candles": deque(maxlen=500), "price": 0.0}
        self._running = True
        log.info("Data feed starting for: %s", list(self.symbols.keys()))
        for sym in self.symbols:
            await self._backfill_historical(sym)
        while self._running:
            try:
                async with websockets.connect(
                    self.ws_url, ping_interval=20, ping_timeout=10
                ) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1
                    log.info("WebSocket connected")
                    async for raw in ws:
                        try:
                            await self._handle(json.loads(raw))
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                log.warning("WS disconnected: %s, reconnecting in %ds", e, self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay)

    async def _backfill_historical(self, symbol):
        if len(self.symbols[symbol]["candles"]) >= 50:
            return
        try:
            ohlcv = await exchange.fetch_ohlcv(
                symbol, config.exchange.default_timeframe, limit=200)
            self.symbols[symbol]["candles"].clear()
            for c in ohlcv:
                candle = Candle(int(c[0] // 1000), c[1], c[2], c[3], c[4], c[5])
                self.symbols[symbol]["candles"].append(candle)
            if self.symbols[symbol]["candles"]:
                self.symbols[symbol]["price"] = self.symbols[symbol]["candles"][-1].close
            log.info("Historical candles %s: %d", symbol, len(self.symbols[symbol]["candles"]))
        except Exception as e:
            log.error("Historical data failed %s: %s", symbol, e)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _handle(self, d):
        stream = d.get("stream", "")
        if not stream:
            return
        s_lower = stream.split("@")[0]
        symbol = None
        for sym in config.exchange.symbols:
            if sym.lower().replace("/", "").replace(":usdt", "") == s_lower:
                symbol = sym
                break
        if not symbol:
            return
        sym_data = self.symbols[symbol]
        data = d["data"]
        e = data.get("e")
        if e == "24hrTicker":
            p = float(data["c"])
            sym_data["price"] = p
            self._emit("ticker", {
                "symbol": symbol, "price": p,
                "change_pct": float(data.get("P", 0)),
                "high": float(data.get("h", 0)),
                "low": float(data.get("l", 0)),
                "volume": float(data.get("v", 0))
            })
        elif e == "kline":
            k = data["k"]
            c = Candle(
                int(k["t"]) // 1000, float(k["o"]), float(k["h"]),
                float(k["l"]), float(k["c"]), float(k["v"])
            )
            sym_data["price"] = c.close
            if sym_data["candles"] and sym_data["candles"][-1].timestamp == c.timestamp:
                sym_data["candles"][-1] = c
            else:
                sym_data["candles"].append(c)
            self._emit("kline", {"symbol": symbol, "candle": c.to_dict()})
        elif e == "trade":
            self._emit("trade", {
                "symbol": symbol, "price": float(data["p"]),
                "amount": float(data["q"]),
                "side": "sell" if data["m"] else "buy"
            })


data_feed = DataFeed()

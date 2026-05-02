import ccxt, asyncio, logging, time
from typing import List
from app.config import config
from app.models import OrderRequest, PositionInfo, OrderInfo

log = logging.getLogger("exchange")


class Exchange:
    def __init__(self):
        p = {
            "apiKey": config.exchange.api_key,
            "secret": config.exchange.secret,
            "enableRateLimit": True,
            "defaultType": "future", "adjustForTimeDifference": True, "warnOnFetchOpenOrdersWithoutSymbol": False}


        if config.exchange.testnet:
            p["sandbox"] = True
        self.client: ccxt.binance = ccxt.binance(p)
        self._ok = False
        # FIX: Position cache to reduce API calls (was 5 calls/min for 5 symbols)
        self._positions_cache: List[PositionInfo] = []
        self._positions_cache_time: float = 0
        self._positions_cache_ttl: float = config.risk.position_cache_ttl

    async def init(self):
        if not self._ok:
            await asyncio.to_thread(self.client.load_markets)
            self._ok = True
            log.info("Exchange initialized | testnet=%s", config.exchange.testnet)

    async def _retry_on_rate_limit(self, func, *args, **kwargs):
        """FIX: Retry with exponential backoff on rate limit errors."""
        import ccxt as ccxt_lib
        for attempt in range(config.risk.api_rate_limit_retries):
            try:
                return await asyncio.to_thread(func, *args, **kwargs)
            except (ccxt_lib.RateLimitExceeded, ccxt_lib.NetworkError) as e:
                delay = config.risk.api_retry_base_delay * (2 ** attempt)
                log.warning("Rate limited (attempt %d/%d), retrying in %.1fs: %s",
                            attempt + 1, config.risk.api_rate_limit_retries, delay, e)
                await asyncio.sleep(delay)
        raise RuntimeError(f"API call failed after {config.risk.api_rate_limit_retries} retries")

    async def fetch_ticker(self, symbol):
        return await self._retry_on_rate_limit(self.client.fetch_ticker, symbol)

    async def fetch_ohlcv(self, symbol, tf="15m", limit=200):
        return await self._retry_on_rate_limit(self.client.fetch_ohlcv, symbol, tf, limit=limit)

    async def fetch_balance(self):
        b = await self._retry_on_rate_limit(self.client.fetch_balance)
        return {
            "total": float(b.get("total", {}).get("USDT", 0)),
            "free": float(b.get("free", {}).get("USDT", 0)),
            "used": float(b.get("used", {}).get("USDT", 0))
        }

    async def fetch_positions(self) -> List[PositionInfo]:
        ps = await self._retry_on_rate_limit(self.client.fetch_positions)
        r = []
        for p in ps:
            c = float(p.get("contracts", 0) or 0)
            if c == 0:
                continue
            r.append(PositionInfo(
                symbol=p["symbol"], side=p["side"], amount=abs(c),
                entry_price=float(p.get("entryPrice", 0)),
                mark_price=float(p.get("markPrice", 0)),
                leverage=int(p.get("leverage", 1)),
                unrealized_pnl=float(p.get("unrealizedPnl", 0)),
                roe=float(p.get("percentage", 0)),
                liquidation_price=float(p.get("liquidationPrice", 0) or 0)
            ))
        return r

    async def fetch_positions_cached(self) -> List[PositionInfo]:
        """FIX: Cached position fetch - reduces API calls from N-per-minute to 1-per-TTL."""
        now = time.time()
        if now - self._positions_cache_time < self._positions_cache_ttl and self._positions_cache:
            return self._positions_cache
        self._positions_cache = await self.fetch_positions()
        self._positions_cache_time = now
        return self._positions_cache

    async def set_leverage(self, symbol, lev):
        return await self._retry_on_rate_limit(
            self.client.set_leverage, min(lev, config.risk.max_leverage), symbol)

    async def place_order(self, req: OrderRequest):
        await self.set_leverage(req.symbol, req.leverage)
        params = {}
        if req.stop_loss:
            params["stopLossPrice"] = req.stop_loss
        if req.take_profit:
            params["takeProfitPrice"] = req.take_profit
        return await self._retry_on_rate_limit(
            self.client.create_order,
            symbol=req.symbol, type=req.type, side=req.side,
            amount=req.amount,
            price=req.price if req.type == "limit" else None,
            params=params
        )

    async def cancel_order(self, order_id: str, symbol: str):
        return await self._retry_on_rate_limit(self.client.cancel_order, order_id, symbol)

    async def fetch_open_orders(self, symbol=None):
        orders = await self._retry_on_rate_limit(self.client.fetch_open_orders, symbol)
        return [OrderInfo(
            id=o["id"], symbol=o["symbol"], side=o["side"], type=o["type"],
            amount=o["amount"], price=o["price"] if o["price"] else 0,
            status=o["status"],
            timestamp=o["timestamp"] / 1000 if o["timestamp"] else time.time()
        ) for o in orders]

    async def close_position(self, symbol, side):
        for p in await self.fetch_positions_cached():
            if p.symbol == symbol and p.side == side:
                return await self._retry_on_rate_limit(
                    self.client.create_order,
                    symbol=symbol, type="market",
                    side="sell" if side == "long" else "buy",
                    amount=p.amount, params={"reduceOnly": True}
                )
        raise ValueError(f"Position not found: {symbol} {side}")

    async def create_stop_order(self, symbol, side, amount, stop_price):
        return await self._retry_on_rate_limit(
            self.client.create_order,
            symbol=symbol, type="stop_market", side=side, amount=amount,
            params={"stopPrice": stop_price, "reduceOnly": True}
        )

    async def fetch_funding_rate(self, symbol):
        try:
            fr = await self._retry_on_rate_limit(self.client.fetch_funding_rate, symbol)
            return float(fr.get("fundingRate", 0) or 0)
        except Exception as e:
            log.warning("Funding rate fetch failed %s: %s", symbol, e)
            return 0.0


exchange = Exchange()

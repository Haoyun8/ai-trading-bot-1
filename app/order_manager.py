import logging, time, asyncio
from app.exchange import exchange
from app.risk_manager import risk_manager
from app.notifier import send_telegram
from app.config import config
from app.data_feed import data_feed
from app.database import save_trade

log = logging.getLogger("order_mgr")


class OrderManager:
    def __init__(self):
        self.active_orders = {}
        self.position_stops = {}
        self._pending_entries = {}  # Track pending limit orders for PnL

    async def monitor_orders(self):
        while True:
            try:
                open_orders = await exchange.fetch_open_orders()
                now = time.time()
                for o in open_orders:
                    if o.type != "limit":
                        continue
                    age = now - o.timestamp
                    if age > config.risk.limit_order_timeout:
                        log.warning("Limit order timeout %s (%.0fs), cancelling", o.id, age)
                        try:
                            await exchange.cancel_order(o.id, o.symbol)
                            await send_telegram(
                                f"\u23f0 Limit order timeout {o.side} {o.amount} @ {o.price}")
                        except Exception as e:
                            log.error("Cancel order failed: %s", e)
            except Exception as e:
                log.error("Order monitor error: %s", e)
            await asyncio.sleep(10)

    async def manage_trailing_stops(self):
        while True:
            try:
                positions = await exchange.fetch_positions_cached()
                for pos in positions:
                    price = data_feed.symbols.get(pos.symbol, {}).get("price", 0)
                    if not price:
                        continue
                    # Fetch ATR for adaptive trailing stop
                    atr = None
                    try:
                        sym_data = data_feed.symbols.get(pos.symbol)
                        if sym_data and len(sym_data["candles"]) >= 20:
                            from app.indicators import candles_to_df, calc_all
                            import asyncio as _aio
                            df = await _aio.to_thread(candles_to_df, list(sym_data["candles"]))
                            ind = await _aio.to_thread(calc_all, df)
                            atr = ind.get("atr", {}).get("value")
                    except Exception:
                        pass

                    new_sl = risk_manager.update_trailing_stop(
                        pos.symbol, pos.side, pos.entry_price, price, atr=atr)
                    if new_sl is None:
                        continue
                    existing = self.position_stops.get(pos.symbol, {}).get(pos.side)
                    stop_side = "sell" if pos.side == "long" else "buy"
                    try:
                        order = await exchange.create_stop_order(
                            pos.symbol, stop_side, pos.amount, new_sl)
                        new_id = order["id"]
                        if existing:
                            try:
                                await exchange.cancel_order(existing, pos.symbol)
                            except Exception:
                                pass
                        self.position_stops.setdefault(pos.symbol, {})[pos.side] = new_id
                        log.info("Trailing stop updated %s %s -> %.2f (ATR=%s)",
                                 pos.symbol, pos.side, new_sl,
                                 f"{atr:.2f}" if atr else "N/A")
                    except Exception as e:
                        log.error("Trailing stop update failed: %s", e)
            except Exception as e:
                log.error("Trailing stop loop error: %s", e)
            await asyncio.sleep(10)

    async def execute_signal(self, signal):
        if not config.auto_trade:
            return
        positions = await exchange.fetch_positions_cached()  # FIX: use cached
        check = risk_manager.check_signal(signal, positions)
        if not check["allowed"]:
            log.info("Auto-trade rejected %s: %s", signal.symbol, check["reason"])
            return

        amount = check["adjusted_amount"]
        side = "buy" if signal.direction == "buy" else "sell"
        price_point = data_feed.symbols.get(signal.symbol, {}).get("price", 0)
        if not price_point:
            return

        order_type = "market"
        limit_price = None

        # FIX: Corrected limit order logic
        # If signal price is BELOW current price for a BUY -> use limit (better price)
        # If signal price is ABOVE current price for a SELL -> use limit (better price)
        # Otherwise use market (don't miss the move)
        if signal.direction == "buy":
            if signal.entry_price < price_point:
                # Price expected to drop to our level -> limit order
                limit_price = signal.entry_price
                order_type = "limit"
            else:
                # Price moving up -> market to not miss it
                order_type = "market"
        elif signal.direction == "sell":
            if signal.entry_price > price_point:
                # Price expected to rise to our level -> limit order
                limit_price = signal.entry_price
                order_type = "limit"
            else:
                # Price moving down -> market to not miss it
                order_type = "market"

        try:
            from app.models import OrderRequest
            req = OrderRequest(
                symbol=signal.symbol, side=side, type=order_type,
                amount=amount, price=limit_price,
                leverage=config.exchange.default_leverage,
                stop_loss=signal.stop_loss, take_profit=signal.take_profit
            )
            order = await exchange.place_order(req)
            log.info("Auto order %s: %s %s @ %s", signal.symbol, side, amount,
                     limit_price or "market")
            await send_telegram(
                f"\U0001f916 Auto open {side} {amount} {signal.symbol} "
                f"@ {limit_price or 'market'}")
        except Exception as e:
            log.error("Auto order failed: %s", e)

    async def manual_close(self, symbol, side):
        """Close a position and record PnL."""
        try:
            result = await exchange.close_position(symbol, side)
            # Record the trade
            positions = await exchange.fetch_positions()
            # The position should now be gone, record PnL from the result
            pnl = float(result.get("info", {}).get("realizedPnl", 0) or 0)
            fee = float(result.get("fee", {}).get("cost", 0) or 0)
            await risk_manager.record_trade(pnl, fee)
            await save_trade({
                "timestamp": time.time(), "symbol": symbol, "side": side,
                "amount": float(result.get("amount", 0)),
                "entry": float(result.get("average", 0) or result.get("price", 0)),
                "exit_price": float(result.get("average", 0) or result.get("price", 0)),
                "pnl": pnl, "fee": fee, "status": "closed"
            })
            log.info("Position closed %s %s pnl=%.2f", symbol, side, pnl)
            await send_telegram(f"\U0001f4c9 Closed {side} {symbol} PnL: ${pnl:.2f}")
            return result
        except Exception as e:
            log.error("Close position failed: %s", e)
            raise


order_manager = OrderManager()

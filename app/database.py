import aiosqlite, logging, os, json, time, asyncio
from datetime import date
from app.config import config

log = logging.getLogger("db")

# FIX: Connection pool to prevent connection leaks under high concurrency
class DatabasePool:
    """Singleton database connection pool. Reuses one connection with write lock."""
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._initialized = False

    async def get(self) -> aiosqlite.Connection:
        async with self._lock:
            if self._db is None or self._initialized is False:
                os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
                self._db = await aiosqlite.connect(self._db_path)
                self._db.row_factory = aiosqlite.Row
                await self._db.execute("PRAGMA journal_mode=WAL")
                await self._db.execute("PRAGMA busy_timeout=5000")
                await self._init_tables()
                self._initialized = True
            return self._db

    async def _init_tables(self):
        db = self._db
        await db.execute("""CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, symbol TEXT, side TEXT,
            amount REAL, entry REAL, exit_price REAL, pnl REAL, fee REAL DEFAULT 0,
            closed INTEGER DEFAULT 1, status TEXT DEFAULT 'closed')""")
        await db.execute("""CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, symbol TEXT, direction TEXT,
            confidence REAL, entry REAL, stop_loss REAL, take_profit REAL, reasoning TEXT)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY, pnl REAL DEFAULT 0, trades INTEGER DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS risk_state (
            id INTEGER PRIMARY KEY CHECK (id=1), daily_pnl REAL DEFAULT 0,
            last_date TEXT, consecutive_losses INTEGER DEFAULT 0, last_loss_time REAL DEFAULT 0)""")
        await db.execute("""CREATE TABLE IF NOT EXISTS strategy_params (
            symbol TEXT, timeframe TEXT, params_json TEXT, sharpe REAL,
            last_updated TEXT, PRIMARY KEY (symbol, timeframe))""")
        await db.execute("""CREATE TABLE IF NOT EXISTS equity_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL, equity REAL)""")
        await db.execute("INSERT OR IGNORE INTO risk_state (id) VALUES (1)")
        await db.commit()

    async def close(self):
        async with self._lock:
            if self._db:
                await self._db.close()
                self._db = None
                self._initialized = False

pool = DatabasePool(config.db_path)

async def save_signal(signal):
    db = await pool.get()
    await db.execute("INSERT INTO signals VALUES (?,?,?,?,?,?,?,?)",
        (None, signal.timestamp, signal.symbol, signal.direction, signal.confidence,
         signal.entry_price, signal.stop_loss, signal.take_profit, signal.reasoning))
    await db.commit()

async def save_trade(trade):
    db = await pool.get()
    await db.execute(
        "INSERT INTO trades (timestamp, symbol, side, amount, entry, exit_price, pnl, fee, status) VALUES (?,?,?,?,?,?,?,?,?)",
        (trade.get("timestamp", 0), trade["symbol"], trade["side"], trade["amount"],
         trade["entry"], trade.get("exit_price", 0), trade["pnl"],
         trade.get("fee", 0), trade.get("status", "closed")))
    today = date.today().isoformat()
    await db.execute(
        "INSERT INTO daily_stats (date, pnl, trades) VALUES (?,?,1) "
        "ON CONFLICT(date) DO UPDATE SET pnl=pnl+excluded.pnl, trades=trades+1",
        (today, trade["pnl"]))
    await db.commit()

async def save_risk_state(daily_pnl, consecutive_losses, last_loss_time, current_date):
    db = await pool.get()
    await db.execute(
        "UPDATE risk_state SET daily_pnl=?, consecutive_losses=?, last_loss_time=?, last_date=? WHERE id=1",
        (daily_pnl, consecutive_losses, last_loss_time, current_date))
    await db.commit()

async def load_risk_state():
    db = await pool.get()
    cursor = await db.execute("SELECT * FROM risk_state WHERE id=1")
    row = await cursor.fetchone()
    if row:
        return {"daily_pnl": row["daily_pnl"] or 0.0, "last_date": row["last_date"],
                "consecutive_losses": row["consecutive_losses"] or 0,
                "last_loss_time": row["last_loss_time"] or 0.0}
    return {"daily_pnl": 0.0, "last_date": None, "consecutive_losses": 0, "last_loss_time": 0.0}

async def save_strategy_params(symbol, timeframe, params, sharpe):
    db = await pool.get()
    await db.execute(
        "INSERT INTO strategy_params VALUES (?,?,?,?,datetime('now')) "
        "ON CONFLICT(symbol,timeframe) DO UPDATE SET params_json=excluded.params_json, sharpe=excluded.sharpe, last_updated=datetime('now')",
        (symbol, timeframe, json.dumps(params), sharpe))
    await db.commit()

async def load_strategy_params(symbol, timeframe):
    db = await pool.get()
    cursor = await db.execute(
        "SELECT * FROM strategy_params WHERE symbol=? AND timeframe=?", (symbol, timeframe))
    row = await cursor.fetchone()
    if row and row["params_json"]:
        return {"params": json.loads(row["params_json"]), "sharpe": row["sharpe"]}
    return None

async def save_equity_snapshot(equity):
    db = await pool.get()
    await db.execute("INSERT INTO equity_history (timestamp, equity) VALUES (?,?)",
        (time.time(), equity))
    await db.commit()

async def get_recent_trades(limit=50):
    db = await pool.get()
    return await db.execute_fetchall(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))

async def get_equity_curve(days=7):
    db = await pool.get()
    rows = await db.execute_fetchall(
        "SELECT date, SUM(pnl) OVER (ORDER BY date) as equity "
        "FROM daily_stats WHERE date >= date('now', ?) ORDER BY date ASC",
        (f'-{days} days',))
    return [{"date": r["date"], "equity": r["equity"]} for r in rows]

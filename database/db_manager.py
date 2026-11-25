import sqlite3
import os
from datetime import datetime, timedelta

import pandas as pd

from core.config import DB_PATH, SCHEMA_PATH

class DBManager:
    def __init__(self, db_path: str = DB_PATH, schema_path: str = SCHEMA_PATH):
        self.db_path = db_path
        self.schema_path = schema_path
        self._initialize_db()

    def _get_connection(self):
        """Creates and returns a connection to the SQLite database."""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize_db(self):
        """Reads schema.sql and creates tables if they don't exist."""
        if not os.path.exists(self.schema_path):
            raise FileNotFoundError(f"Schema file not found at {self.schema_path}")
            
        with open(self.schema_path, 'r') as f:
            schema_script = f.read()

        with self._get_connection() as conn:
            # WAL lets the bot, the scheduler and (later) the API use the DB at the same time
            conn.execute("PRAGMA journal_mode=WAL")
            cursor = conn.cursor()
            cursor.executescript(schema_script)
            conn.commit()

    def log_suggested_trade(self, ticker: str, action: str, suggested_price: float, target_price: float, stop_loss: float, reasoning: str):
        """Called by the AI Agent to log a new trade recommendation."""
        query = """
            INSERT INTO trade_history (ticker, date_suggested, action, suggested_price, target_price, stop_loss, status, agent_reasoning)
            VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)
        """
        date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (ticker, date_now, action, suggested_price, target_price, stop_loss, reasoning))
            conn.commit()
            return cursor.lastrowid

    def update_execution(self, trade_id: int, actual_price: float, capital: float):
        """
        UPDATED: Now includes capital_allocated.
        Called via Telegram when you confirm you have bought the asset.
        """
        query = """
            UPDATE trade_history 
            SET actual_execution_price = ?, capital_allocated = ?, status = 'ACTIVE'
            WHERE id = ?
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (actual_price, capital, trade_id))
            conn.commit()

    def update_feedback(self, trade_id: int, status: str, pnl_percentage: float, user_feedback: str):
        """Called when you sell the asset and report the result."""
        valid_statuses = ['CLOSED_WIN', 'CLOSED_LOSS', 'MISSED']
        if status not in valid_statuses:
            raise ValueError(f"Status must be one of {valid_statuses}")

        query = """
            UPDATE trade_history 
            SET status = ?, pnl_percentage = ?, user_feedback = ?
            WHERE id = ?
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (status, pnl_percentage, user_feedback, trade_id))
            conn.commit()

    def get_pending_trades(self):
        """Fetches trades that are either suggested (PENDING) or live (ACTIVE)."""
        query = "SELECT * FROM trade_history WHERE status IN ('PENDING', 'ACTIVE')"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]
        
    def get_trade_by_id(self, trade_id: int):
        """Fetches a single trade record by its ID. Optimized and cleaned."""
        query = "SELECT * FROM trade_history WHERE id = ?"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, (trade_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------
    # Instruments (watchlist + holdings)
    # ------------------------------------------------------------------
    def get_instruments(self, market: str | None = None, active_only: bool = True) -> list[dict]:
        query = "SELECT * FROM instruments WHERE 1=1"
        params: list = []
        if active_only:
            query += " AND active = 1"
        if market:
            query += " AND market = ?"
            params.append(market)
        query += " ORDER BY market, symbol"
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def add_instrument(self, symbol: str, market: str, asset_type: str, currency: str, is_holding: bool = False):
        query = """
            INSERT INTO instruments (symbol, market, asset_type, currency, is_holding, active)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(symbol) DO UPDATE SET active = 1, is_holding = excluded.is_holding
        """
        with self._get_connection() as conn:
            conn.execute(query, (symbol.upper(), market, asset_type, currency, int(is_holding)))

    # ------------------------------------------------------------------
    # Price + FX history
    # ------------------------------------------------------------------
    def upsert_price_bars(self, symbol: str, df: pd.DataFrame) -> int:
        rows = [
            (symbol, idx.strftime("%Y-%m-%d"), r.Open, r.High, r.Low, r.Close, r.Volume)
            for idx, r in df.iterrows()
        ]
        query = """
            INSERT INTO price_bars (symbol, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                open = excluded.open, high = excluded.high, low = excluded.low,
                close = excluded.close, volume = excluded.volume
        """
        with self._get_connection() as conn:
            conn.executemany(query, rows)
        return len(rows)

    def get_price_history(self, symbol: str, days: int | None = None) -> pd.DataFrame:
        query = "SELECT date, open, high, low, close, volume FROM price_bars WHERE symbol = ? ORDER BY date"
        with self._get_connection() as conn:
            df = pd.read_sql_query(query, conn, params=(symbol.upper(),))
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").rename(columns=str.capitalize)
        return df.tail(days) if days else df

    def upsert_fx(self, pair: str, series: pd.Series) -> int:
        rows = [(pair, idx.strftime("%Y-%m-%d"), float(v)) for idx, v in series.dropna().items()]
        query = """
            INSERT INTO fx_rates (pair, date, rate) VALUES (?, ?, ?)
            ON CONFLICT(pair, date) DO UPDATE SET rate = excluded.rate
        """
        with self._get_connection() as conn:
            conn.executemany(query, rows)
        return len(rows)

    def get_fx_history(self, pair: str, days: int | None = None) -> pd.Series:
        with self._get_connection() as conn:
            df = pd.read_sql_query(
                "SELECT date, rate FROM fx_rates WHERE pair = ? ORDER BY date", conn, params=(pair,)
            )
        if df.empty:
            return pd.Series(dtype=float, name=pair)
        s = df.set_index(pd.to_datetime(df["date"]))["rate"].rename(pair)
        return s.tail(days) if days else s

    def get_latest_fx(self, pair: str) -> float | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT rate FROM fx_rates WHERE pair = ? ORDER BY date DESC LIMIT 1", (pair,)
            ).fetchone()
        return row["rate"] if row else None

    # ------------------------------------------------------------------
    # Alert de-duplication
    # ------------------------------------------------------------------
    def should_fire_alert(self, alert_key: str, cooldown_hours: float, now: datetime | None = None) -> bool:
        """True if this alert has never fired or its cooldown has passed."""
        now = now or datetime.now()
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT last_fired_at FROM alert_state WHERE alert_key = ?", (alert_key,)
            ).fetchone()
        if row is None:
            return True
        last = datetime.fromisoformat(row["last_fired_at"])
        return now - last >= timedelta(hours=cooldown_hours)

    def mark_alert_fired(self, alert_key: str, value: float | None = None, now: datetime | None = None):
        now = now or datetime.now()
        query = """
            INSERT INTO alert_state (alert_key, last_fired_at, last_value) VALUES (?, ?, ?)
            ON CONFLICT(alert_key) DO UPDATE SET
                last_fired_at = excluded.last_fired_at, last_value = excluded.last_value
        """
        with self._get_connection() as conn:
            conn.execute(query, (alert_key, now.isoformat(timespec="seconds"), value))

    def clear_alert(self, alert_key: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM alert_state WHERE alert_key = ?", (alert_key,))

# Quick test execution block
if __name__ == "__main__":
    db = DBManager()
    print("Database initialized successfully.")
    
    # Test logging a trade
    # trade_id = db.log_suggested_trade("ASII", "BUY", 5000, 5075, 4900, "Volume spike detected in last 10 mins.")
    # print(f"Logged mock trade with ID: {trade_id}")
    
    # # Test fetching pending trades
    # pending = db.get_pending_trades()
    # print(f"Pending trades: {pending}")
import sqlite3
import os
from datetime import datetime, timedelta

import pandas as pd

from core.config import DB_PATH, SCHEMA_PATH

class DBManager:
    def __init__(self, db_path: str = DB_PATH, schema_path: str = SCHEMA_PATH):
        self.db_path = db_path
        self.schema_path = schema_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
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

    def set_instrument_active(self, symbol: str, active: bool) -> bool:
        with self._get_connection() as conn:
            cur = conn.execute("UPDATE instruments SET active = ? WHERE symbol = ?",
                               (int(active), symbol.upper()))
            return cur.rowcount > 0

    def get_instrument(self, symbol: str) -> dict | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM instruments WHERE symbol = ?", (symbol.upper(),)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Fundamentals
    # ------------------------------------------------------------------
    FUNDAMENTAL_FIELDS = ("sector", "industry", "market_cap", "market_cap_usd", "pe", "forward_pe",
                          "pb", "roe", "roa", "der", "div_yield", "net_margin", "rev_growth",
                          "eps_growth")

    def upsert_fundamentals(self, symbol: str, as_of: str, source: str, values: dict):
        cols = ["symbol", "as_of", "source", *self.FUNDAMENTAL_FIELDS]
        params = [symbol.upper(), as_of, source, *(values.get(f) for f in self.FUNDAMENTAL_FIELDS)]
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols[2:])
        query = (f"INSERT INTO fundamentals ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                 f"ON CONFLICT(symbol, as_of) DO UPDATE SET {updates}")
        with self._get_connection() as conn:
            conn.execute(query, params)

    def get_latest_fundamentals(self, symbol: str) -> dict | None:
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM fundamentals WHERE symbol = ? ORDER BY as_of DESC LIMIT 1",
                (symbol.upper(),)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # ETF profiles
    # ------------------------------------------------------------------
    def upsert_etf_profile(self, symbol: str, as_of: str, source: str, data: dict):
        import json
        query = """
            INSERT INTO etf_profiles (symbol, as_of, source, data) VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                as_of = excluded.as_of, source = excluded.source, data = excluded.data
        """
        with self._get_connection() as conn:
            conn.execute(query, (symbol.upper(), as_of, source, json.dumps(data)))

    def get_etf_profile(self, symbol: str) -> dict | None:
        """Returns the stored profile dict plus 'as_of', or None."""
        import json
        with self._get_connection() as conn:
            row = conn.execute("SELECT as_of, data FROM etf_profiles WHERE symbol = ?",
                               (symbol.upper(),)).fetchone()
        if row is None:
            return None
        return {**json.loads(row["data"]), "as_of": row["as_of"]}

    # ------------------------------------------------------------------
    # Screens
    # ------------------------------------------------------------------
    def get_screens(self, market: str | None = None, active_only: bool = True) -> list[dict]:
        """market='IDX' returns IDX screens plus the ones that run on every market."""
        query = "SELECT * FROM screens WHERE 1=1"
        params: list = []
        if active_only:
            query += " AND active = 1"
        if market:
            query += " AND (market = ? OR market IS NULL)"
            params.append(market)
        with self._get_connection() as conn:
            return [dict(r) for r in conn.execute(query + " ORDER BY name", params).fetchall()]

    def get_screen(self, name: str) -> dict | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM screens WHERE name = ?", (name.lower(),)).fetchone()
        return dict(row) if row else None

    def save_screen(self, name: str, rule: str, market: str | None = None):
        query = """
            INSERT INTO screens (name, rule, market, active) VALUES (?, ?, ?, 1)
            ON CONFLICT(name) DO UPDATE SET rule = excluded.rule, market = excluded.market, active = 1
        """
        with self._get_connection() as conn:
            conn.execute(query, (name.lower(), rule, market))
            # A changed rule starts fresh, so every current match is reported as new once
            conn.execute("DELETE FROM screen_hits WHERE screen = ?", (name.lower(),))

    def delete_screen(self, name: str) -> bool:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM screen_hits WHERE screen = ?", (name.lower(),))
            return conn.execute("DELETE FROM screens WHERE name = ?", (name.lower(),)).rowcount > 0

    def record_screen_hits(self, screen: str, symbols_in_scope: list[str], hits: list[str],
                           today: str) -> list[str]:
        """Store the current matches for one screen over the symbols it looked at.
        Returns the symbols that are new matches since the previous run."""
        with self._get_connection() as conn:
            known = {r["symbol"] for r in conn.execute(
                "SELECT symbol FROM screen_hits WHERE screen = ?", (screen,)).fetchall()}
            for sym in hits:
                conn.execute("""
                    INSERT INTO screen_hits (screen, symbol, first_seen, last_seen) VALUES (?, ?, ?, ?)
                    ON CONFLICT(screen, symbol) DO UPDATE SET last_seen = excluded.last_seen
                """, (screen, sym, today, today))
            dropped = [s for s in symbols_in_scope if s in known and s not in hits]
            conn.executemany("DELETE FROM screen_hits WHERE screen = ? AND symbol = ?",
                             [(screen, s) for s in dropped])
        return [s for s in hits if s not in known]

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

    # --- scheduled job runs ------------------------------------------------------------
    def record_job_run(self, job: str, session_date: str, status: str) -> None:
        from datetime import timezone
        with self._get_connection() as conn:
            conn.execute(
                "INSERT INTO job_runs (job, session_date, status, ran_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(job, session_date) DO UPDATE SET status=excluded.status, ran_at=excluded.ran_at",
                (job, session_date, status, datetime.now(timezone.utc).isoformat(timespec="seconds")))

    def get_job_run(self, job: str, session_date: str) -> dict | None:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM job_runs WHERE job = ? AND session_date = ?",
                               (job, session_date)).fetchone()
        return dict(row) if row else None

    def get_last_job_runs(self) -> list[dict]:
        """Latest run per job."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT j.* FROM job_runs j JOIN (SELECT job, MAX(session_date) d FROM job_runs GROUP BY job) m "
                "ON j.job = m.job AND j.session_date = m.d ORDER BY j.job").fetchall()
        return [dict(r) for r in rows]


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

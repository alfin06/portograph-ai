import sqlite3
import os
from datetime import datetime

class DBManager:
    def __init__(self, db_path="database/portograph.db", schema_path="database/schema.sql"):
        self.db_path = db_path
        self.schema_path = schema_path
        self._initialize_db()

    def _get_connection(self):
        """Creates and returns a connection to the SQLite database."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row 
        return conn

    def _initialize_db(self):
        """Reads schema.sql and creates tables if they don't exist."""
        if not os.path.exists(self.schema_path):
            raise FileNotFoundError(f"Schema file not found at {self.schema_path}")
            
        with open(self.schema_path, 'r') as f:
            schema_script = f.read()

        with self._get_connection() as conn:
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
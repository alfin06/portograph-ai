-- Table to track your overall capital and daily performance
CREATE TABLE IF NOT EXISTS portfolio_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    starting_capital REAL NOT NULL,
    ending_capital REAL,
    daily_pnl_percentage REAL,
    notes TEXT
);

-- Table to log every trade the agent suggests and you execute
CREATE TABLE IF NOT EXISTS trade_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    date_suggested TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
    suggested_price REAL NOT NULL,
    actual_execution_price REAL DEFAULT 0,
    capital_allocated REAL DEFAULT 0, -- NEW: Track how much IDR you actually spent
    target_price REAL DEFAULT 0,
    stop_loss REAL DEFAULT 0,
    status TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'ACTIVE', 'CLOSED_WIN', 'CLOSED_LOSS', 'MISSED')),
    pnl_percentage REAL DEFAULT 0,
    agent_reasoning TEXT,
    user_feedback TEXT 
);

-- Insert your starting capital (e.g., 10 million IDR)
INSERT OR IGNORE INTO portfolio_state (date, starting_capital) 
VALUES (date('now'), 10000000.0);
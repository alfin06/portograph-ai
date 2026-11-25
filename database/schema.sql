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

-- =====================================================================
-- Multi-market investing (Phase 0)
-- =====================================================================

-- Everything the app tracks: your holdings plus anything you want to watch
CREATE TABLE IF NOT EXISTS instruments (
    symbol TEXT PRIMARY KEY,                -- plain symbol, e.g. BBCA, VOO
    market TEXT NOT NULL CHECK(market IN ('IDX', 'US')),
    asset_type TEXT NOT NULL CHECK(asset_type IN ('STOCK', 'ETF')),
    currency TEXT NOT NULL CHECK(currency IN ('IDR', 'USD')),
    is_holding INTEGER NOT NULL DEFAULT 0,  -- 1 = you own it, 0 = watch only
    active INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Daily OHLCV bars, one row per symbol per trading day
CREATE TABLE IF NOT EXISTS price_bars (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,                     -- YYYY-MM-DD (exchange local date)
    open REAL, high REAL, low REAL, close REAL NOT NULL, volume REAL,
    PRIMARY KEY (symbol, date)
);

-- FX history, e.g. USDIDR
CREATE TABLE IF NOT EXISTS fx_rates (
    pair TEXT NOT NULL,
    date TEXT NOT NULL,
    rate REAL NOT NULL,
    PRIMARY KEY (pair, date)
);

-- Remembers when each alert last fired so Telegram is not spammed
CREATE TABLE IF NOT EXISTS alert_state (
    alert_key TEXT PRIMARY KEY,             -- e.g. "trade:12:STOP_LOSS"
    last_fired_at TEXT NOT NULL,
    last_value REAL
);

-- Starter watchlist: current holdings
INSERT OR IGNORE INTO instruments (symbol, market, asset_type, currency, is_holding) VALUES
    ('BBCA', 'IDX', 'STOCK', 'IDR', 1),
    ('BBRI', 'IDX', 'STOCK', 'IDR', 1),
    ('BMRI', 'IDX', 'STOCK', 'IDR', 1),
    ('ASII', 'IDX', 'STOCK', 'IDR', 1),
    ('ITMG', 'IDX', 'STOCK', 'IDR', 1),
    ('ADRO', 'IDX', 'STOCK', 'IDR', 1),
    ('ULTJ', 'IDX', 'STOCK', 'IDR', 1),
    ('VOO',  'US',  'ETF',   'USD', 1),
    ('QQQM', 'US',  'ETF',   'USD', 1);

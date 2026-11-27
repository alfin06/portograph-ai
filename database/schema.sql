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

-- =====================================================================
-- Fundamentals + screener (Phase 2)
-- =====================================================================

-- One row per symbol per fetch date. Percent fields are in percent (15 = 15%),
-- der is a plain ratio (0.8 = 0.8x), market_cap is in the trading currency.
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,                    -- YYYY-MM-DD fetched
    source TEXT NOT NULL,
    sector TEXT, industry TEXT,
    market_cap REAL, market_cap_usd REAL,
    pe REAL, forward_pe REAL, pb REAL,
    roe REAL, roa REAL, der REAL, div_yield REAL,
    net_margin REAL, rev_growth REAL, eps_growth REAL,
    PRIMARY KEY (symbol, as_of)
);

-- Saved screens, e.g. "roe > 15 and pb < 2". market NULL = run on both markets.
CREATE TABLE IF NOT EXISTS screens (
    name TEXT PRIMARY KEY,
    rule TEXT NOT NULL,
    market TEXT CHECK(market IN ('IDX', 'US') OR market IS NULL),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Symbols currently matching each screen; a symbol that drops out is removed,
-- so matching again later counts as a new hit
CREATE TABLE IF NOT EXISTS screen_hits (
    screen TEXT NOT NULL,
    symbol TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (screen, symbol)
);

-- Starter screens (edit or delete them from Telegram with /addscreen, /delscreen)
INSERT OR IGNORE INTO screens (name, rule, market) VALUES
    ('value_idx', 'pe < 12 and pb < 2 and roe > 15', 'IDX'),
    ('dividend', 'div_yield > 5 and roe > 12', NULL),
    ('oversold_quality', 'rsi < 35 and roe > 12', NULL);

-- =====================================================================
-- ETF profiles (Phase 3)
-- =====================================================================

-- Latest profile per ETF as JSON: expense ratio, AUM, category, sector
-- weights and top holdings (Yahoo only publishes the top 10)
CREATE TABLE IF NOT EXISTS etf_profiles (
    symbol TEXT PRIMARY KEY,
    as_of TEXT NOT NULL,                    -- YYYY-MM-DD fetched
    source TEXT NOT NULL,
    data TEXT NOT NULL
);

-- =====================================================================
-- Scheduled job runs (local hosting)
-- =====================================================================

-- One row per scheduled digest outcome, so a digest missed while the computer
-- was off or asleep is sent once on wake-up, and never twice
CREATE TABLE IF NOT EXISTS job_runs (
    job TEXT NOT NULL,                      -- e.g. digest_IDX
    session_date TEXT NOT NULL,             -- YYYY-MM-DD the run was for
    status TEXT NOT NULL,                   -- sent | no_data | failed
    ran_at TEXT NOT NULL,                   -- ISO timestamp, UTC
    PRIMARY KEY (job, session_date)
);

-- =====================================================================
-- Valuation, buy zones and sizing
-- =====================================================================

-- Per stock: P/E, P/B and dividend-yield distributions over the last ~5 years
-- (from unadjusted prices and reported book value / earnings), as JSON
CREATE TABLE IF NOT EXISTS valuation_bands (
    symbol TEXT PRIMARY KEY,
    as_of TEXT NOT NULL,                    -- YYYY-MM-DD computed
    source TEXT NOT NULL,
    data TEXT NOT NULL
);

-- Price ranges you'd consider buying in; the digest tells you when a close enters one.
-- state is where the last close was: above | inside | below (NULL = not checked yet)
CREATE TABLE IF NOT EXISTS price_zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    low REAL NOT NULL,
    high REAL NOT NULL,
    note TEXT,
    created TEXT NOT NULL,
    state TEXT,
    state_date TEXT
);

-- Small key/value settings changed from Telegram (e.g. capital for /size)
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- =====================================================================
-- Portfolio tracking
-- =====================================================================

-- Your own buys, sells and dividends. Positions, average cost and profit are worked
-- out from this ledger (average-cost method, as IDX brokers report it).
-- shares are real shares (IDX lots x 100); price and fee are in the trading currency;
-- amount is the net cash of a dividend; fx_idr is IDR per unit of that currency on the
-- day (1 for IDR), so IDR profit includes what the exchange rate did.
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,                     -- YYYY-MM-DD
    kind TEXT NOT NULL CHECK(kind IN ('BUY', 'SELL', 'DIV')),
    shares REAL NOT NULL DEFAULT 0,
    price REAL NOT NULL DEFAULT 0,
    fee REAL NOT NULL DEFAULT 0,
    amount REAL,
    fx_idr REAL,
    note TEXT,
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transactions_symbol ON transactions (symbol, date, id);

-- One row per day: the portfolio's value at that day's closes. Written by each market
-- digest; the US digest later overwrites the same date so a day ends with both closes.
CREATE TABLE IF NOT EXISTS portfolio_history (
    date TEXT PRIMARY KEY,                  -- YYYY-MM-DD of the latest close used
    value_idr REAL NOT NULL,
    cost_idr REAL NOT NULL,                 -- open positions' cost at buy-day rates
    unrealised_idr REAL NOT NULL,
    realised_idr REAL NOT NULL,             -- running totals
    income_idr REAL NOT NULL,
    usdidr REAL,
    recorded_at TEXT NOT NULL
);

-- =====================================================================
-- Dividend calendar
-- =====================================================================

-- Ex-dividend dates per symbol: the past ~3 years plus announced upcoming ones.
-- amount is per share in the trading currency; NULL = date announced, amount not yet.
CREATE TABLE IF NOT EXISTS dividends (
    symbol TEXT NOT NULL,
    ex_date TEXT NOT NULL,                  -- YYYY-MM-DD
    amount REAL,
    source TEXT NOT NULL,
    fetched TEXT NOT NULL,                  -- YYYY-MM-DD
    PRIMARY KEY (symbol, ex_date)
);

# PortoGraph AI

A lightweight trading assistant that combines Telegram-driven approvals, rule-based screening, and Google Generative AI for reflective analysis. Designed for personal, educational and research use.

Overview
- **Purpose:** Automate daily market scans, notify setups via Telegram, and iterate on screening rules using a Critic Agent.
- **Core components:** Telegram listener, Screener Agent (rules-based), Critic Agent (reflection & rule proposals), backtester for strategy validation.

Folder structure
- **app/**: multi-market investing core (IDX, US stocks, US ETFs)
  - `app/providers/`: data sources behind one interface (`yahoo.py` prices, `yahoo_fundamentals.py` ratios)
  - `app/jobs/`: jobs (`update_prices.py`, `update_fundamentals.py`, `digest.py`)
  - `app/analytics/`: shared numbers (returns, RSI, 52-week range, signals, screener, sentiment, ETF overlap, currency-adjusted returns) used by the bot, jobs and future web app
  - `app/commands.py`: logic behind the Telegram commands (testable without Telegram)
  - `app/reports/`: message formatting; `app/notify/`: Telegram push
  - `app/worker.py`: scheduler that sends the daily close digests
  - `app/symbols.py`: symbol mapping (BBCA -> BBCA.JK, FX pairs)
- **agents/**: Critic Agent (to be repurposed for reviewing buy/sell zones)
- **core/**: config and the Telegram bot ([core/telegram_bot.py](core/telegram_bot.py))
- **data_pipeline/**: technical indicators and the legacy IDX scraper
- **database/**: DB manager and schema ([database/schema.sql](database/schema.sql)); SQLite in WAL mode
- **modules/crypto/**: optional Indodax crypto module (scanner, LangGraph, price watcher)
- **tests/**: pytest suite (no network or credentials needed)

Quickstart
1. Create and activate a virtual environment

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in the Forge and Telegram keys.

4. Run everything **from the project root with `python -m`**:

```bash
python -m pytest                                 # tests
python -m app.jobs.update_prices                 # fetch 2y of prices for the watchlist + USD/IDR
python -m app.jobs.update_prices --period 1mo    # daily refresh
python -m core.telegram_bot                      # Telegram bot (keep running)
python -m app.worker                             # digest scheduler (keep running)
```

Daily digests
- The worker sends an **IDX close digest** at 16:30 WIB and a **US close digest** at 17:00 New York time (about 05:00 WIB), Mon-Fri. Change the times with `DIGEST_IDX_TIME` / `DIGEST_US_TIME`.
- Each digest has a price table (holdings marked •), best/worst movers, signals (±5% day, RSI ≤30 / ≥70, volume ≥2× the 5-day average, new 52-week high/low) and USD/IDR.
- If the closing bar isn't available yet, the worker retries 3 times, 20 minutes apart, then sends a one-line notice (holiday or late feed).
- Preview without sending: `python -m app.jobs.digest --market IDX --dry-run`. On demand from Telegram: `/digest idx` or `/digest us`.
- `python -m app.worker --list` shows the next run times.

Fundamentals and screens
- Ratios come from Yahoo and are refreshed weekly per symbol (the digest fetches any that are older than 7 days, and new symbols right away). Manual refresh: `python -m app.jobs.update_fundamentals --all`.
- Units: ROE, margins, growth and dividend yield in percent (15 = 15%); `der` is a ratio (0.8 = 0.8x). When a company reports in another currency than it trades in (e.g. ITMG reports in USD), P/B is recomputed with USD/IDR because Yahoo's own value is wrong.
- A screen is a rule such as `pe < 12 and pb < 2 and roe > 15`. Rules accept comparisons joined by `and` / `or`; a missing value (banks have no D/E) never matches. Fields: `close chg_1d chg_5d chg_1m rsi vol_surge from_52w_high holding pe forward_pe pb roe roa der div_yield net_margin rev_growth eps_growth market_cap_usd_bn`.
- Every digest lists each screen's matches for that market; symbols that just started matching are marked 🆕. `--dry-run` never consumes the 🆕 markers.
- Starter screens: `value_idx`, `dividend`, `oversold_quality`. Screens only look at symbols on your watchlist, so add candidates with `/watch`.

Market mood (fear & greed)
- Every digest opens with a 0-100 mood score for that market (0 = extreme fear, 100 = extreme greed, CNN's bands), last week's value, and the inputs pushing it most. `/sentiment` shows every input.
- It is our own estimate from free Yahoo data (CNN's index blocks automated access). Each input is ranked against its own past year, flipped where high means fear, then averaged:
  - US: S&P 500 vs its 125-day average, VIX vs its 50-day average, stocks vs Treasuries (SPY-IEF, 20d), high-yield vs Treasuries (HYG-IEF, 20d)
  - IDX: IHSG vs its 125-day average, IHSG 20-day volatility, USD/IDR 20-day change, EIDO vs S&P 500 (20d) as a proxy for foreign appetite for Indonesia
- Per-stock foreign buy/sell is not included: IDX blocks automated access and the API providers that resell it are paid.

ETFs and currency
- `/etf VOO`: expense ratio with its cost in rupiah (per Rp 100 juta a year, and over 10 years vs a zero-fee fund at 7%), AUM, yield, turnover, top 10 holdings and sector mix. Works for any US-listed ETF; profiles are cached for 7 days.
- `/overlap VOO QQQM`: how similar two ETFs are, three ways: daily-return correlation over the last year, sector overlap (whole fund) and top-holdings overlap. Yahoo only lists the top 10 holdings and most issuers block automated holdings downloads, so the holdings figure is a lower bound.
- `/compare BBCA VOO QQQM 1y`: returns in the asset's own currency, in IDR and in USD, plus how much of a US asset's IDR return came from the rupiah moving. Periods: 1m, 3m, 6m, 1y, 2y, ytd. Prices are dividend-adjusted, and every row uses the same end date and currency move so IDX and US rows compare fairly.

Telegram commands
- `/digest idx|us` latest close digest
- `/fund BBCA` fundamentals and price snapshot
- `/watch BBNI idx [etf] [hold]`, `/unwatch BBNI` manage the watchlist (checks Yahoo has data first)
- `/screens` saved screens and fields · `/screen value_idx` or `/screen roe > 15 and pb < 2` run now
- `/addscreen cheap_banks idx pb < 1.5 and roe > 12` save · `/delscreen cheap_banks` delete
- `/sentiment` or `/sentiment idx` fear & greed score with each input
- `/etf VOO`, `/overlap VOO QQQM`, `/compare BBCA VOO 1y` ETF details, ETF similarity, returns in IDR and USD

Optional crypto module:

```bash
python -m modules.crypto.run_daily_scan
python -m modules.crypto.price_watcher
```

Running from mainland China: Telegram and Yahoo Finance need a VPN that stays connected.

Notes & tips
- Keep `dynamic_rules.json` under version control to track rule changes.
- Use the Critic Agent via Telegram (`/reflect`) to propose rule updates and review them before committing.
- Once `TELEGRAM_CHAT_ID` is set, the bot ignores every other chat.

Troubleshooting
- If the bot doesn't connect: verify `TELEGRAM_BOT_TOKEN` and ensure network outbound connections are allowed.
- If chat id is missing: start the bot, send `/start` to your bot, then check logs for the printed chat id.
- Missing dependencies: re-run `pip install -r requirements.txt` or confirm your virtual environment is active.

Disclaimer
- This project is provided for personal and research use only. It is not intended for commercial use, distribution, or deployment. Trading carries risk — validate backtests before using real capital.

# PortoGraph AI

A lightweight trading assistant that combines Telegram-driven approvals, rule-based screening, and Google Generative AI for reflective analysis. Designed for personal, educational and research use.

Overview
- **Purpose:** Automate daily market scans, notify setups via Telegram, and iterate on screening rules using a Critic Agent.
- **Core components:** Telegram listener, Screener Agent (rules-based), Critic Agent (reflection & rule proposals), backtester for strategy validation.

Folder structure
- **app/**: multi-market investing core (IDX, US stocks, US ETFs)
  - `app/providers/`: data sources behind one interface (`yahoo.py` today; more later)
  - `app/jobs/`: jobs (`update_prices.py`, `digest.py`)
  - `app/analytics/`: shared numbers (returns, RSI, 52-week range, signals) used by the bot, jobs and future web app
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

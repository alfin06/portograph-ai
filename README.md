# PortoGraph AI

A lightweight trading assistant that combines Telegram-driven approvals, rule-based screening, and Google Generative AI for reflective analysis. Designed for personal, educational and research use.

Overview
- **Purpose:** Automate daily market scans, notify setups via Telegram, and iterate on screening rules using a Critic Agent.
- **Core components:** Telegram listener, Screener Agent (rules-based), Critic Agent (reflection & rule proposals), backtester for strategy validation.

Folder structure
- **app/**: multi-market investing core (IDX, US stocks, US ETFs)
  - `app/providers/`: data sources behind one interface (`yahoo.py` prices, `yahoo_fundamentals.py` ratios)
  - `app/jobs/`: jobs (`update_prices.py`, `update_fundamentals.py`, `digest.py`)
  - `app/analytics/`: shared numbers (returns, RSI, 52-week range, signals, screener, sentiment, ETF overlap, currency-adjusted returns, valuation bands, buy zones, position sizing) used by the bot, jobs and future web app
  - `app/commands.py`: logic behind the Telegram commands (testable without Telegram)
  - `app/reports/`: message formatting; `app/notify/`: Telegram push
  - `app/worker.py`: scheduler that sends the daily close digests, catches up after sleep, backs up the DB
  - `app/web/`: the web dashboard (FastAPI + Jinja2 + HTMX, charts with TradingView Lightweight Charts, vendored in `app/web/static/vendor`)
  - `app/run.py`: starts and supervises the bot, worker and dashboard (same entry point on this PC and in Docker)
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

3. Copy `.env.example` to `.env` and fill in the Forge and Telegram keys. Send `/start` to your bot to get your chat ID; until `TELEGRAM_CHAT_ID` is set every command is off.

4. Run everything **from the project root with `python -m`**:

```bash
python -m pytest                                 # tests
python -m app.jobs.update_prices                 # fetch 2y of prices for the watchlist + USD/IDR
python -m app.jobs.update_prices --period 1mo    # daily refresh
python -m app.run                               # bot + digest worker together (keep running)
```

Web dashboard
- Open **http://127.0.0.1:8000** while PortoGraph is running. Pages:
  - Overview: watchlist, mood, buy zones hit, screen matches.
  - Stock (click a symbol): candle chart with 50/200-day averages, the valuation band prices (P/B, P/E or yield) and your buy zones drawn on, valuation table, fundamentals, add/delete zones, position-size calculator.
  - Screens (try, save, delete rules), Compare (returns in IDR/USD/own currency with a growth chart), ETFs (profile and overlap), Status.
- Pages read the database only, so they load fast and work while Yahoo or the VPN is down. "Build/Rebuild from Yahoo" on a stock page and the ETF page are the only parts that call Yahoo.
- Only this computer can open it. To use it from your phone on home Wi-Fi, set `WEB_HOST=0.0.0.0` and `WEB_PASSWORD` in `.env`; it refuses to listen beyond this PC without a password. `WEB_ENABLED=false` turns it off. On its own: `python -m app.web`.
- Security: forms only accept requests from the dashboard's own pages, other websites can't load or embed it, and wrong passwords lock you out for 15 minutes after 10 tries. Details and limits in [SECURITY.md](SECURITY.md).
- Lock `.env`, the database and logs to your Windows account: `powershell -ExecutionPolicy Bypass -File scripts\windows\lock-down-files.ps1`

Running it on this PC
- `python -m app.run` starts the Telegram bot, the digest worker and the web dashboard as separate processes, restarts any that crashes, and writes `logs/bot.log`, `logs/worker.log`, `logs/web.log`, `logs/run.log`. `--status` shows what's running; `--stop` shuts it down cleanly. It refuses to start a second copy.
- Start automatically at logon, hidden, no admin needed:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install-autostart.ps1 -StartNow
```

  Remove with `scripts\windows\uninstall-autostart.ps1`.
- Sleep and shutdown: digests only go out while the PC is on. After it wakes or boots, the worker sends a digest it missed if it is under 20 hours late (`CATCHUP_HOURS`), and never sends one twice. The US digest is due around 05:00 WIB, so if the PC sleeps overnight it arrives when you wake it.
- The worker backs up the database every day at 03:15 to `database/backups/` (keeps 14). Make one any time with `python -m app.backup`.
- `/status` in Telegram shows the last digest per market, the newest stored prices and the last backup.

Moving to a server later
1. On this PC: `python -m app.backup --out <folder>`, then `scripts\windows\uninstall-autostart.ps1` so two bots don't run at once.
2. On the server (Linux with Docker): clone the repo, copy `.env` next to `docker-compose.yml`, and copy the backup to `data/portograph.db`.
3. Add `WEB_PASSWORD` to `.env` (the container's dashboard won't start without one).
4. `docker compose up -d --build`. Same code, same command (`python -m app.run`); the database, backups and logs live in `data/`. Logs: `docker compose logs -f`.
5. The dashboard is published on the server's own localhost only. Open it through an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 you@server`, then http://127.0.0.1:8000 on your PC.

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

Valuation, buy zones and position size
- `/value BBCA`: is the stock cheap or expensive versus **its own** last ~5 years? Daily P/E, P/B and dividend yield are rebuilt from unadjusted closes and the book value, earnings and dividends the company had published at the time (figures count from 90 days after a year end, 45 after a quarter end; reporting-currency figures such as ITMG's USD are converted at that day's USD/IDR). Shows today's rank (% of past days lower), the 10th/median/90th percentile, the prices that match the cheap boundary and the typical level, and the ROE trend, with a warning when ROE is falling (cheap because the business is weaker). Bands are cached for 7 days. Stocks only; for ETFs use `/etf`.
- `/zone BBCA 5500 5950 [note]`: a buy zone. Each digest has a 🎯 section listing stocks that closed inside a zone, or fell through one, and marks 🆕 the first time. `/zones` lists them with the distance from the last close; `/delzone ID` deletes. `/value` suggests a zone from the stock's cheapest quarter.
- `/capital 150jt` then `/size BBCA 5800 5400 [1%] [10%]`: lots to buy so that hitting the stop loses at most the risk % of capital (default 1%), capped at the max position % (default 10%). IDX rounds to lots of 100 shares; US amounts are converted with USD/IDR, with a fractional-share figure when not even one share fits. `now` uses the last close as the entry. Fees and gaps through the stop are not included.
- Numbers accept `5500`, `5,500`, `5.500`, `150jt`, `1.5m`, `2b`.

Portfolio tracking
- Record what you actually bought and sold: `/buy BBCA 10 8950 20/11` (IDX quantity in lots), `/buy VOO 1.5 540.20 2026-11-20 fee=0` (US in shares, fractions OK), `/sell ...` the same way, `/div BBRI 1.25jt` for dividend cash received. The date defaults to today. Without `fee=`, IDX fees are estimated at 0.15% on buys and 0.25% on sells (including the 0.1% sales tax).
- `/portfolio`: value, today's change, unrealised and realised profit and dividends, all in IDR; a per-holding table (quantity, average cost, last close, profit %, weight); allocation by sector and currency; warnings when one stock is over 20% of the portfolio (ETFs excluded) or one sector over 40%.
- Average-cost method, as IDX brokers report it. Cost in IDR uses the USD/IDR rate on each buy day and value uses today's, so for US holdings you also see how much of the IDR profit came from the rupiah.
- `/txns [SYM]` lists transactions with their IDs, `/deltxn ID` deletes one. A sale larger than what you held on that day is refused, and so is deleting a buy that a later sale depends on.
- `/price BBCA`: latest close and daily change, 52-week range, the nearest buy zone and your position.
- The dashboard's Portfolio page shows the same, with forms to record trades and dividends; the Overview page shows a one-line summary.
- Stocks already marked as holdings (`hold`) but with no transactions are listed so you can add their buys. Recording trades keeps the holding flag in step.

Telegram commands
- `/digest idx|us` latest close digest
- `/fund BBCA` fundamentals and price snapshot
- `/watch BBNI idx [etf] [hold]`, `/unwatch BBNI` manage the watchlist (checks Yahoo has data first)
- `/screens` saved screens and fields · `/screen value_idx` or `/screen roe > 15 and pb < 2` run now
- `/addscreen cheap_banks idx pb < 1.5 and roe > 12` save · `/delscreen cheap_banks` delete
- `/sentiment` or `/sentiment idx` fear & greed score with each input
- `/etf VOO`, `/overlap VOO QQQM`, `/compare BBCA VOO 1y` ETF details, ETF similarity, returns in IDR and USD
- `/value BBCA` cheap or expensive vs its own history · `/zone`, `/zones`, `/delzone` buy zones · `/capital`, `/size` position size
- `/portfolio` holdings and profit · `/buy`, `/sell`, `/div` record · `/txns`, `/deltxn` list or delete · `/price BBCA` quick quote
- `/portofolio` (old spelling) is the earlier AI swing-trade list, kept as it was
- `/status` is everything running? last digests, newest prices, last backup

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

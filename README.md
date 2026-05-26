# PortoGraph AI

A lightweight trading assistant that combines Telegram-driven approvals, rule-based screening, and Google Generative AI for reflective analysis. Designed for educational and research use with the Indonesian Stock Exchange (IDX).

Overview
- **Purpose:** Automate daily market scans, notify setups via Telegram, and iterate on screening rules using a Critic Agent.
- **Core components:** Telegram listener, Screener Agent (rules-based), Critic Agent (reflection & rule proposals), backtester for strategy validation.

Folder structure
- **agents/**: agent implementations (e.g., critic_agent.py, screener_agent.py)
- **core/**: core utilities and integrations (see [core/telegram_bot.py](core/telegram_bot.py))
- **data_pipeline/**: data collectors and indicator calculations
- **database/**: DB manager and schema ([database/schema.sql](database/schema.sql))
- **scripts/**: runnable scripts ([scripts/run_daily_scan.py](scripts/run_daily_scan.py), [scripts/run_backtest.py](scripts/run_backtest.py))
- `dynamic_rules.json`: active screener rules used by the Screener Agent

Quickstart
1. Create and activate a virtual environment

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Create a `.env` file in the repository root with these keys (example):

```env
GEMINI_API_KEY="your_google_ai_studio_key"
TELEGRAM_BOT_TOKEN="your_botfather_token"
TELEGRAM_CHAT_ID="your_personal_chat_id"
```

4. Run the Telegram listener (keep running for approvals and commands):

```bash
python core/telegram_bot.py
```

Common workflows
- Run the daily market scan (approx. 15:45 WIB):

```bash
python scripts/run_daily_scan.py
```
- Run backtests:

```bash
python scripts/run_backtest.py
```
- Quick system check:

```bash
python scripts/system_check.py
```

Notes & tips
- Keep `dynamic_rules.json` under version control to track rule changes.
- Use the Critic Agent via Telegram (`/reflect`) to propose rule updates and review them before committing.
- Backtest any rule changes before applying to live scanning.

Troubleshooting
- If the bot doesn't connect: verify `TELEGRAM_BOT_TOKEN` and ensure network outbound connections are allowed.
- If chat id is missing: start the bot, send `/start` to your bot, then check logs for the printed chat id.
- Missing dependencies: re-run `pip install -r requirements.txt` or confirm your virtual environment is active.

Disclaimer
- This project is provided for personal and research use only. It is not intended for commercial use, distribution, or deployment. Trading carries risk — validate backtests before using real capital.
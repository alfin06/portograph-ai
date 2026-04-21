# PortoGraph AI

A lightweight trading assistant that combines Telegram-driven approvals, rule-based screening, and Google Generative AI for reflective analysis. Designed for educational and research use with the Indonesian Stock Exchange (IDX).

**Overview**
- **Purpose:** Automate daily market scans, notify setups via Telegram, and iterate on screening rules using a Critic Agent.
- **Core components:** Telegram listener, Screener Agent (rules-based), Critic Agent (reflection & rule proposals), backtester for strategy validation.

**Folder Structure**
- **`agents/`**: agent implementations (e.g., `critic_agent.py`, `screener_agent.py`).
- **`core/`**: core utilities and integrations (see [core/telegram_bot.py](core/telegram_bot.py)).
- **`data_pipeline/`**: data collectors and indicator calculations.
- **`database/`**: DB manager and schema (`database/schema.sql`).
- **`scripts/`**: runnable scripts: [scripts/run_daily_scan.py](scripts/run_daily_scan.py), [scripts/run_backtest.py](scripts/run_backtest.py).
- **`dynamic_rules.json`**: active screener rules used by the Screener Agent.

**Installation & Setup**

1. Clone the repository and create a virtual environment:

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# Mac/Linux:
source .venv/bin/activate
```

2. Install dependencies (ensure `requirements.txt` is up-to-date):

Required packages:
- yfinance
- pandas
- python-dotenv
- python-telegram-bot
- google-generativeai
- langgraph

```bash
pip install -r requirements.txt
```

3. Configure environment variables: create a `.env` file in the repository root with at least the following keys:

```env
GEMINI_API_KEY="your_google_ai_studio_key"
TELEGRAM_BOT_TOKEN="your_botfather_token"
TELEGRAM_CHAT_ID="your_personal_chat_id"
```

(To get your Chat ID, you may leave `TELEGRAM_CHAT_ID` blank, run the bot, then send `/start` to your bot — the bot will log your chat id.)

Daily Operations

1. The Listener (Run in Background)
- Keep the Telegram bot running in a dedicated terminal to receive commands, approvals, and rule updates.

```bash
python core/telegram_bot.py
```

2. The Execution (15:45 WIB)
- In a separate terminal, run the daily market scan ~15 minutes before IDX close. If a setup is found it will notify your Telegram with entry/exit details.

```bash
python scripts/run_daily_scan.py
```

3. Strategy Validation (Backtesting)
- Before using real capital, simulate the strategy over historical data to verify performance over recent market regimes.

```bash
python scripts/run_backtest.py
```

4. Agent Reflection (Weekly)
- Send `/reflect` to the Telegram bot. The Critic Agent will analyze closed trades and suggest risk-management rules. Approve proposals to persist them into `dynamic_rules.json` for future scans.

**Notes & Tips**
- Keep `dynamic_rules.json` under version control if you want to track rule evolution.
- Run backtests after rule changes to ensure the proposal improves performance before using live.
- Configure logging and backups for your database (`database/`) if you plan to persist trade history.

**Disclaimer**
This project is provided for educational and personal research purposes only. Quantitative trading carries significant financial risk. Always validate backtest results and understand the strategy before executing live trades on the Indonesian Stock Exchange.

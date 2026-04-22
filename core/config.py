import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Forge API Credentials (Primary Engine)
FORGE_API_KEY = os.getenv("FORGE_API_KEY")
FORGE_BASE_URL = os.getenv("FORGE_BASE_URL")

# --- Fail-fast validation ---
if not FORGE_API_KEY or not FORGE_BASE_URL:
    print("⚠️ Warning: Forge API credentials missing in .env.")

if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
    raise ValueError("CRITICAL: Telegram credentials missing.")

# --- Trading Strategy Parameters ---
STARTING_CAPITAL = 10000000.0  # 10 Million IDR
TARGET_MULTIPLIER = 1.015      # 1.5% target
STOP_LOSS_MULTIPLIER = 0.98    # 2.0% stop loss
ESTIMATED_FEES = 0.004         # 0.4% friction

# --- Agent Configuration ---
# Pulls the Forge model name (e.g., tensorblock/gpt-5-mini)
LLM_MODEL = os.getenv("MODEL_NAME", "tensorblock/gpt-5-mini")
DB_PATH = "database/portograph.db"
RULES_PATH = "dynamic_rules.json"
import os
from dotenv import load_dotenv

# Automatically find and load the .env file when this module is imported
load_dotenv()

# --- API Keys ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Fail-fast validation: Crash immediately if critical keys are missing
if not GEMINI_API_KEY:
    raise ValueError("CRITICAL: GEMINI_API_KEY is missing from the .env file.")

# --- Trading Strategy Parameters ---
STARTING_CAPITAL = 10000000.0  # 10 Million IDR
TARGET_MULTIPLIER = 1.015      # 1.5% gross target (aiming for ~1% net)
STOP_LOSS_MULTIPLIER = 0.98    # 2.0% stop loss
ESTIMATED_FEES = 0.004         # ~0.4% total round-trip friction (broker + tax)

# --- Agent Configuration ---
LLM_MODEL = "gemini-2.5-flash"
DB_PATH = "database/portograph.db"
RULES_PATH = "dynamic_rules.json"
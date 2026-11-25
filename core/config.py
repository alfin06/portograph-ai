"""Central configuration. Values come from .env; nothing here raises at import time,
so tests and tools can import modules without real credentials.
Call require_telegram() / require_llm() in the entry points that actually need them."""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env(key: str, default: str = "") -> str:
    v = os.getenv(key, default)
    return (v or default).strip().strip('"').strip("'")


# --- API Keys ---
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID")
FORGE_API_KEY = _env("FORGE_API_KEY")
FORGE_BASE_URL = _env("FORGE_BASE_URL")
LLM_MODEL = _env("MODEL_NAME", "tensorblock/gpt-5-mini")

# --- Modules ---
ENABLE_CRYPTO = _env("ENABLE_CRYPTO", "false").lower() in ("1", "true", "yes")

# --- Alerts ---
ALERT_COOLDOWN_HOURS = float(_env("ALERT_COOLDOWN_HOURS", "12") or 12)

# --- Digests (HH:MM in each exchange's own time zone, Mon-Fri) ---
DIGEST_IDX_TIME = _env("DIGEST_IDX_TIME", "16:30")  # Asia/Jakarta; IDX closes 16:00
DIGEST_US_TIME = _env("DIGEST_US_TIME", "17:00")    # America/New_York; NYSE closes 16:00

# --- Crypto strategy parameters (used only by modules/crypto) ---
STARTING_CAPITAL = 10_000_000.0  # IDR
TARGET_MULTIPLIER = 1.015
STOP_LOSS_MULTIPLIER = 0.98
ESTIMATED_FEES = 0.004

# --- Paths (absolute, so scripts work from any working directory) ---
DB_PATH = str(PROJECT_ROOT / "database" / "portograph.db")
SCHEMA_PATH = str(PROJECT_ROOT / "database" / "schema.sql")
RULES_PATH = str(PROJECT_ROOT / "dynamic_rules.json")


def require_telegram() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in .env")


def require_llm() -> None:
    if not FORGE_API_KEY or not FORGE_BASE_URL:
        raise RuntimeError("FORGE_API_KEY / FORGE_BASE_URL missing in .env")

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
# Runtime data lives in DATA_DIR. Locally it defaults to database/; in Docker it is a
# mounted volume (/app/data), so moving to a server means copying one folder plus .env.
DATA_DIR = Path(_env("DATA_DIR") or PROJECT_ROOT / "database")
DB_PATH = str(DATA_DIR / "portograph.db")
BACKUP_DIR = Path(_env("BACKUP_DIR") or DATA_DIR / "backups")
BACKUP_KEEP = int(_env("BACKUP_KEEP", "14") or 14)          # daily copies to keep
LOG_DIR = Path(_env("LOG_DIR") or PROJECT_ROOT / "logs")
CATCHUP_HOURS = float(_env("CATCHUP_HOURS", "20") or 20)    # send a missed digest up to this late
SCHEMA_PATH = str(PROJECT_ROOT / "database" / "schema.sql")
RULES_PATH = str(PROJECT_ROOT / "dynamic_rules.json")

# --- Web dashboard ---
# Default: only this computer can open it. Anything else (LAN, a server) needs WEB_PASSWORD.
WEB_HOST = _env("WEB_HOST", "127.0.0.1") or "127.0.0.1"
WEB_PORT = int(_env("WEB_PORT", "8000") or 8000)
WEB_PASSWORD = _env("WEB_PASSWORD")
WEB_ENABLED = _env("WEB_ENABLED", "true").lower() not in ("0", "false", "no")
# Hostnames the browser may use to reach the dashboard (besides localhost), e.g. your PC's
# LAN address "192.168.1.20". Blocks DNS-rebinding attacks from websites you visit.
WEB_ALLOWED_HOSTS = [h.strip().lower() for h in _env("WEB_ALLOWED_HOSTS").split(",") if h.strip()]
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MIN_WEB_PASSWORD = 12


def web_problem() -> str | None:
    """Why the web dashboard must not start with these settings, or None if it may."""
    if WEB_PASSWORD and len(WEB_PASSWORD) < MIN_WEB_PASSWORD:
        return f"WEB_PASSWORD must be at least {MIN_WEB_PASSWORD} characters"
    if WEB_HOST not in LOOPBACK_HOSTS and not WEB_PASSWORD:
        return (f"WEB_HOST={WEB_HOST} makes the dashboard reachable from other machines; "
                "set WEB_PASSWORD in .env first")
    return None


def require_telegram() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in .env")


def require_llm() -> None:
    if not FORGE_API_KEY or not FORGE_BASE_URL:
        raise RuntimeError("FORGE_API_KEY / FORGE_BASE_URL missing in .env")

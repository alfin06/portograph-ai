"""Watches ACTIVE crypto trades and alerts on Telegram when stop loss / take profit is hit.

Fixes vs. the old version:
- each alert fires once, then waits ALERT_COOLDOWN_HOURS before repeating
- uses asyncio.sleep, so it no longer blocks the event loop
- fetches the market snapshot once per loop instead of once per trade

Run: python -m modules.crypto.price_watcher
"""
import asyncio
import logging

from telegram import Bot

from core.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ALERT_COOLDOWN_HOURS, require_telegram
from database.db_manager import DBManager
from modules.crypto.scraper import CryptoScraper

log = logging.getLogger("price_watcher")

IDLE_SLEEP_S = 300    # no active trades
ACTIVE_SLEEP_S = 120  # at least one active trade


def _norm(t: str) -> str:
    return t.replace("/", "").replace("_", "").upper()


def check_trade(trade: dict, price: float) -> str | None:
    """Return 'STOP_LOSS', 'TAKE_PROFIT' or None. Pure function, easy to test."""
    if trade.get("stop_loss") and price <= trade["stop_loss"]:
        return "STOP_LOSS"
    if trade.get("target_price") and price >= trade["target_price"]:
        return "TAKE_PROFIT"
    return None


def format_alert(kind: str, trade: dict, price: float) -> str:
    if kind == "STOP_LOSS":
        return (f"🛑 <b>STOP LOSS: {trade['ticker']}</b>\n"
                f"Price {price:,.0f} IDR ≤ stop {trade['stop_loss']:,.0f} IDR.")
    return (f"🎯 <b>TARGET REACHED: {trade['ticker']}</b>\n"
            f"Price {price:,.0f} IDR ≥ target {trade['target_price']:,.0f} IDR.")


async def watch_once(db: DBManager, scraper: CryptoScraper, bot: Bot) -> None:
    active = [t for t in db.get_pending_trades() if t["status"] == "ACTIVE"]
    if not active:
        return
    prices = {_norm(s["ticker"]): s["current_price"] for s in scraper.get_market_snapshot()}

    for trade in active:
        price = prices.get(_norm(trade["ticker"]))
        if price is None:
            log.warning("No price for %s", trade["ticker"])
            continue
        log.info(f"{trade['ticker']}: {price:,.0f} | SL {trade['stop_loss']:,.0f} | TP {trade['target_price']:,.0f}")

        kind = check_trade(trade, price)
        if kind is None:
            continue
        key = f"trade:{trade['id']}:{kind}"
        if not db.should_fire_alert(key, ALERT_COOLDOWN_HOURS):
            continue
        try:
            await bot.send_message(TELEGRAM_CHAT_ID, format_alert(kind, trade, price), parse_mode="HTML")
            db.mark_alert_fired(key, price)
        except Exception as e:
            log.error("Telegram send failed: %s", e)


async def run_watcher():
    require_telegram()
    db, scraper, bot = DBManager(), CryptoScraper(), Bot(token=TELEGRAM_BOT_TOKEN)
    log.info("PortoGraph crypto watcher started")
    while True:
        try:
            await watch_once(db, scraper, bot)
        except Exception as e:
            log.exception("Watcher loop error: %s", e)
        has_active = any(t["status"] == "ACTIVE" for t in db.get_pending_trades())
        await asyncio.sleep(ACTIVE_SLEEP_S if has_active else IDLE_SLEEP_S)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run_watcher())

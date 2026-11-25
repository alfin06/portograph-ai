"""End-of-day digest for one market: refresh prices, build snapshots, send to Telegram.

Run from the project root:
    python -m app.jobs.digest --market IDX --dry-run   # print instead of sending
    python -m app.jobs.digest --market US
"""
import argparse
import logging
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.analytics import sentiment
from app.analytics.screener import run_screens
from app.analytics.snapshot import fx_change, market_snapshots
from app.jobs import update_fundamentals, update_prices
from app.reports.digest import format_digest
from database.db_manager import DBManager

log = logging.getLogger("digest")

MARKET_TZ = {"IDX": ZoneInfo("Asia/Jakarta"), "US": ZoneInfo("America/New_York")}
FULL_HISTORY_BARS = 260  # below this, backfill 2y so 52-week stats are real


@dataclass
class DigestResult:
    status: str          # 'sent' | 'no_session_data'
    text: str | None = None


def market_today(market: str, now: datetime | None = None) -> date:
    now = now or datetime.now(tz=MARKET_TZ[market])
    return now.astimezone(MARKET_TZ[market]).date()


def _refresh_period(db: DBManager, market: str) -> str:
    for inst in db.get_instruments(market=market):
        if len(db.get_price_history(inst["symbol"])) < FULL_HISTORY_BARS:
            return "2y"
    return "1mo"


def run(market: str, notifier, db: DBManager | None = None, provider=None,
        session_date: date | None = None, refresh: bool = True,
        fund_provider=None, record_hits: bool = True) -> DigestResult:
    """session_date: the trading day the digest is for. When given and no instrument has
    a bar for that day (holiday, or the feed is late), nothing is sent and the caller may
    retry. When None, the digest covers the latest stored session."""
    db = db or DBManager()
    if refresh:
        if provider is None:
            from app.providers.yahoo import YahooProvider
            provider = YahooProvider()
        summary = update_prices.run(period=_refresh_period(db, market), provider=provider,
                                    db=db, market=market)
        if summary["missing"]:
            log.warning("%s: no data for %s", market, ", ".join(summary["missing"]))
        try:  # weekly-stale or brand-new symbols only; a failure must not block the digest
            fs = update_fundamentals.run(db=db, provider=fund_provider, market=market)
            if fs["missing"]:
                log.warning("%s: no fundamentals for %s", market, ", ".join(fs["missing"]))
        except Exception:
            log.exception("%s: fundamentals refresh failed", market)
        try:
            sentiment.refresh(db, provider, market)
        except Exception:
            log.exception("%s: sentiment inputs refresh failed", market)

    snaps = market_snapshots(db, market)
    if session_date is not None:
        target = pd.Timestamp(session_date)
        if not any(s.date == target for s in snaps):
            log.info("%s: no bars for %s yet", market, session_date)
            return DigestResult("no_session_data")
    else:
        target = max((s.date for s in snaps), default=pd.Timestamp(market_today(market)))

    # record_hits=False (dry runs) leaves 🆕 markers for the next real digest
    screens = run_screens(db, market, snaps, today=target.date().isoformat(), record=record_hits)
    try:
        mood = sentiment.from_db(db, market)
    except Exception:
        log.exception("%s: sentiment failed", market)
        mood = None
    text = format_digest(market, snaps, target, fx=fx_change(db.get_fx_history("USDIDR", days=10)),
                         screens=screens, mood=mood)
    notifier.send(text)
    return DigestResult("sent", text)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--market", choices=["IDX", "US"], required=True)
    p.add_argument("--dry-run", action="store_true", help="print the digest instead of sending it")
    p.add_argument("--no-refresh", action="store_true", help="use stored prices, skip the download")
    args = p.parse_args()

    if args.dry_run:
        from app.notify.telegram import ConsoleNotifier
        notifier = ConsoleNotifier()
    else:
        from app.notify.telegram import TelegramNotifier
        notifier = TelegramNotifier()
    run(args.market, notifier, refresh=not args.no_refresh, record_hits=not args.dry_run)


if __name__ == "__main__":
    main()

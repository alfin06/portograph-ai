"""End-of-day digest for one market: refresh prices, build snapshots, send to Telegram.

Run from the project root:
    python -m app.jobs.digest --market IDX --dry-run   # print instead of sending
    python -m app.jobs.digest --market US
"""
import argparse
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from app.analytics import dividends, macro_calendar, portfolio, sentiment, zones
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


DIV_DAYS = 7      # dividends going ex this soon get a line
MACRO_DAYS = 3    # rate decisions, data releases and holidays this soon


@dataclass
class ComingUp:
    went_ex: list          # holdings in this market that went ex on the session day
    dividends: list        # this market's holdings going ex in the next DIV_DAYS
    events: list           # macro events and holidays in the next MACRO_DAYS
    calendar_stale: bool


def coming_up(db: DBManager, market: str, session_day: date) -> ComingUp | None:
    cal = dividends.calendar(db, today=session_day, days=DIV_DAYS, market=market)
    went_ex = [e for e in cal["upcoming"] if e.ex_date == session_day and e.status == "announced"]
    # an ex-date on the session day itself shows as 'went ex', not as upcoming
    upcoming = [e for e in cal["upcoming"] if e.ex_date > session_day]
    events, _ = macro_calendar.load()
    soon = macro_calendar.upcoming(events, session_day + timedelta(days=1), MACRO_DAYS - 1)
    stale = macro_calendar.needs_update(events, session_day)
    if not (went_ex or upcoming or soon or stale):
        return None
    return ComingUp(went_ex, upcoming, soon, stale)


def run(market: str, notifier, db: DBManager | None = None, provider=None,
        session_date: date | None = None, refresh: bool = True,
        fund_provider=None, record_hits: bool = True, div_provider=None) -> DigestResult:
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
        try:  # holdings only, once a day
            failed = dividends.refresh(db, market=market, provider=div_provider)
            if failed:
                log.warning("%s: no dividend data for %s", market, ", ".join(failed))
        except Exception:
            log.exception("%s: dividends refresh failed", market)

    # A catch-up digest (sent late) still reports the session it is for
    snaps = market_snapshots(db, market, until=None if session_date is None else pd.Timestamp(session_date))
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
    try:
        zone_events = zones.check(db, market, snaps, target, record=record_hits)
    except Exception:
        log.exception("%s: buy-zone check failed", market)
        zone_events = []
    try:
        port = portfolio.digest_section(db, market, target, record=record_hits)
    except Exception:
        log.exception("%s: portfolio section failed", market)
        port = None
    try:
        coming = coming_up(db, market, target.date())
    except Exception:
        log.exception("%s: calendar failed", market)
        coming = None
    text = format_digest(market, snaps, target, fx=fx_change(db.get_fx_history("USDIDR", days=10)),
                         screens=screens, mood=mood, zones=zone_events, portfolio=port, coming=coming)
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

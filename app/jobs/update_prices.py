"""Fetch daily prices for every active instrument plus USD/IDR and store them.

Run from the project root:
    python -m app.jobs.update_prices            # last 2 years (first run)
    python -m app.jobs.update_prices --period 1mo
"""
import argparse
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.providers.yahoo import YahooProvider
from app.symbols import Instrument
from database.db_manager import DBManager

log = logging.getLogger("update_prices")


def run(period: str = "2y", provider=None, db: DBManager | None = None,
        market: str | None = None) -> dict:
    """market=None updates everything; 'IDX' or 'US' updates just that market."""
    db = db or DBManager()
    provider = provider or YahooProvider()

    instruments = [
        Instrument(r["symbol"], r["market"], r["asset_type"], r["currency"])
        for r in db.get_instruments(market=market)
    ]
    history = provider.get_history(instruments, period=period)

    summary = {"updated": {}, "missing": []}
    for inst in instruments:
        df = history.get(inst.symbol)
        if df is None or df.empty:
            summary["missing"].append(inst.symbol)
            continue
        summary["updated"][inst.symbol] = db.upsert_price_bars(inst.symbol, df)

    fx = provider.get_fx("USDIDR", period=period)
    summary["fx_rows"] = db.upsert_fx("USDIDR", fx) if not fx.empty else 0
    return summary


# Local close time plus a buffer for Yahoo to publish the final bar
SESSION_DONE = {"IDX": ("Asia/Jakarta", time(16, 30)), "US": ("America/New_York", time(16, 30))}


def last_closed_session(market: str, now: datetime | None = None) -> date:
    """Most recent weekday whose session has finished (exchange holidays aren't known,
    so on a holiday this is a day with no bar and the top-up simply finds nothing new)."""
    tz_name, done = SESSION_DONE[market]
    local = (now or datetime.now(tz=ZoneInfo(tz_name))).astimezone(ZoneInfo(tz_name))
    d = local.date() if local.time() >= done else local.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def top_up(db: DBManager, instruments: list[Instrument], provider=None,
           now: datetime | None = None) -> list[str]:
    """Fetch recent bars for instruments whose stored prices stop before the last finished
    session, so on-demand commands don't wait for the next scheduled digest. Bars for a
    session still in progress are dropped (they'd be intraday, not closes).
    Returns the symbols that got new bars."""
    stale = []
    for inst in instruments:
        hist = db.get_price_history(inst.symbol)
        if hist.empty or hist.index[-1].date() < last_closed_session(inst.market, now):
            stale.append(inst)
    if not stale:
        return []
    provider = provider or YahooProvider()
    got = provider.get_history(stale, period="1mo")
    updated = []
    for inst in stale:
        df = got.get(inst.symbol)
        if df is None or df.empty:
            continue
        df = df[df.index.date <= last_closed_session(inst.market, now)]
        if not df.empty:
            db.upsert_price_bars(inst.symbol, df)
            updated.append(inst.symbol)
    if updated:
        try:
            fx = provider.get_fx("USDIDR", period="1mo")
            if fx is not None and not fx.empty:
                db.upsert_fx("USDIDR", fx)
        except Exception as e:  # FX is a nice-to-have here; stored FX still works
            log.warning("USDIDR top-up failed: %s", e)
    return updated


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--period", default="2y", help="yfinance period, e.g. 1mo, 6mo, 2y")
    p.add_argument("--market", choices=["IDX", "US"], help="only update one market")
    args = p.parse_args()

    s = run(period=args.period, market=args.market)
    for sym, n in s["updated"].items():
        print(f"  [OK] {sym}: {n} bars")
    for sym in s["missing"]:
        print(f"  [!]  {sym}: no data (check symbol / VPN)")
    print(f"  USDIDR: {s['fx_rows']} rows")


if __name__ == "__main__":
    main()

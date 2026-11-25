"""Fetch fundamental ratios for active instruments and store today's values.

Run from the project root:
    python -m app.jobs.update_fundamentals              # only symbols older than 7 days
    python -m app.jobs.update_fundamentals --all        # refetch everything
The daily digest also calls this, so new symbols get fundamentals automatically.
"""
import argparse
import logging
from datetime import date, timedelta

from app.symbols import Instrument
from database.db_manager import DBManager

log = logging.getLogger("update_fundamentals")

MAX_AGE_DAYS = 7  # ratios move with quarterly reports; weekly is plenty


def stale_instruments(db: DBManager, market: str | None, today: date,
                      max_age_days: int = MAX_AGE_DAYS) -> list[Instrument]:
    cutoff = (today - timedelta(days=max_age_days)).isoformat()
    out = []
    for r in db.get_instruments(market=market):
        latest = db.get_latest_fundamentals(r["symbol"])
        if latest is None or latest["as_of"] <= cutoff:
            out.append(Instrument(r["symbol"], r["market"], r["asset_type"], r["currency"]))
    return out


def run(db: DBManager | None = None, provider=None, market: str | None = None,
        refetch_all: bool = False, today: date | None = None) -> dict:
    db = db or DBManager()
    today = today or date.today()
    if refetch_all:
        todo = [Instrument(r["symbol"], r["market"], r["asset_type"], r["currency"])
                for r in db.get_instruments(market=market)]
    else:
        todo = stale_instruments(db, market, today)
    if not todo:
        return {"updated": [], "missing": []}

    if provider is None:
        from app.providers.yahoo_fundamentals import YahooFundamentals
        provider = YahooFundamentals()
    data = provider.get_fundamentals(todo, usdidr=db.get_latest_fx("USDIDR"))
    for sym, values in data.items():
        db.upsert_fundamentals(sym, today.isoformat(), provider.name, values)
    return {"updated": sorted(data), "missing": sorted(i.symbol for i in todo if i.symbol not in data)}


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--market", choices=["IDX", "US"])
    p.add_argument("--all", action="store_true", help="refetch even if fresh")
    args = p.parse_args()
    s = run(market=args.market, refetch_all=args.all)
    print(f"  updated: {', '.join(s['updated']) or '-'}")
    if s["missing"]:
        print(f"  missing: {', '.join(s['missing'])}")


if __name__ == "__main__":
    main()

"""Fetch daily prices for every active instrument plus USD/IDR and store them.

Run from the project root:
    python -m app.jobs.update_prices            # last 2 years (first run)
    python -m app.jobs.update_prices --period 1mo
"""
import argparse
import logging

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

"""Dividend calendar for what you hold.

  - announced: an upcoming ex-date the company published (Yahoo), with the amount when known
  - expected:  no announcement yet, but the stock or ETF went ex around this time last
               year; the date and amount are last year's, so treat them as a rough guide
  - to record: an ex-date that passed while you held the stock, with no dividend recorded
               since; the cash usually arrives 2-4 weeks later (IDX), then /div records it

To get a dividend you must own the shares at the close of the trading day BEFORE the
ex-date. Amounts are before tax: Indonesia withholds 10% on IDX dividends for individuals,
and the US withholds 15% on US ETF dividends for Indonesian residents with a W-8BEN form
on file at the broker (30% without one).
"""
from dataclasses import dataclass
from datetime import date, timedelta

from app.analytics import portfolio as pf

TAX_PCT = {"IDR": 10.0, "USD": 15.0}
LOOKAHEAD_DAYS = 45
RECORD_WINDOW_DAYS = 60       # how long a passed ex-date waits for its /div
MATCH_DAYS = 25               # an announced date this close to last year's replaces the estimate
REFRESH_DAYS = 1


@dataclass
class DividendEvent:
    symbol: str
    market: str
    currency: str
    ex_date: date
    per_share: float | None
    status: str                 # announced | expected | to_record
    shares: float
    basis: str = ""             # e.g. "last year 30 Dec"

    @property
    def gross(self) -> float | None:
        return None if self.per_share is None else self.per_share * self.shares

    @property
    def net(self) -> float | None:
        g = self.gross
        return None if g is None else g * (1 - TAX_PCT.get(self.currency, 0) / 100)

    @property
    def cum_date(self) -> date:
        """Last day to own it: the weekday before the ex-date (holidays not checked)."""
        d = self.ex_date - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    @property
    def lots(self) -> float:
        return self.shares / pf.LOT.get(self.market, 1)


# --- refreshing --------------------------------------------------------------------------
def refresh(db, market: str | None = None, provider=None, today: date | None = None,
            force: bool = False) -> list[str]:
    """Fetch dividends for the holdings (in one market, or all) not fetched today.
    Returns the symbols that failed; one failure never stops the others."""
    from app.symbols import Instrument

    today = today or date.today()
    if provider is None:
        from app.providers.yahoo_dividends import YahooDividends
        provider = YahooDividends()
    failed = []
    for i in db.get_instruments(market=market):
        if not i["is_holding"]:
            continue
        last = db.get_setting(f"dividends_fetched:{i['symbol']}")
        if not force and last and (today - date.fromisoformat(last)).days < REFRESH_DAYS:
            continue
        inst = Instrument(i["symbol"], i["market"], i["asset_type"], i["currency"])
        try:
            rows = provider.get(inst.yahoo, today)
        except Exception:
            failed.append(i["symbol"])
            continue
        db.save_dividends(i["symbol"], rows, "yahoo", today.isoformat())
    return failed


# --- reading -----------------------------------------------------------------------------
def _held_on(txns: list[dict], day: date, usdidr) -> float:
    """Shares held at the close before `day` (the ex-date)."""
    before = [t for t in txns if t["date"] < day.isoformat()]
    if not before:
        return 0.0
    p = pf.replay(before, usdidr).get(txns[0]["symbol"])
    return p.shares if p else 0.0


def _recorded_since(txns: list[dict], day: date) -> bool:
    return any(t["kind"] == "DIV" and t["date"] >= day.isoformat() for t in txns)


def calendar(db, today: date | None = None, days: int = LOOKAHEAD_DAYS,
             market: str | None = None) -> dict[str, list[DividendEvent]]:
    """{'upcoming': announced + expected in the next `days`, 'to_record': passed ex-dates
    awaiting a /div}, for holdings only, each sorted by ex-date."""
    today = today or date.today()
    usdidr = db.get_latest_fx("USDIDR")
    port = pf.replay(db.get_transactions(), usdidr)
    insts = {i["symbol"]: i for i in db.get_instruments(active_only=False)}
    upcoming, to_record = [], []
    horizon = today + timedelta(days=days)
    for sym, inst in insts.items():
        if market and inst["market"] != market:
            continue
        txns = db.get_transactions(sym)
        pos = port.get(sym)
        held_now = pos.shares if pos and pos.open else 0.0
        if held_now <= 0 and not txns:
            continue
        rows = db.get_dividends(sym)
        mk = lambda d, amt, status, shares, basis="": DividendEvent(
            sym, inst["market"], inst["currency"], d, amt, status, shares, basis)

        announced = [(date.fromisoformat(r["ex_date"]), r["amount"]) for r in rows
                     if today <= date.fromisoformat(r["ex_date"]) <= horizon]
        if held_now > 0:
            for d, amt in announced:
                upcoming.append(mk(d, amt, "announced", held_now))
            # last year's ex-dates that fall in the window a year on, not yet announced
            for r in rows:
                past = date.fromisoformat(r["ex_date"])
                try:
                    guess = past.replace(year=past.year + 1)
                except ValueError:               # 29 Feb
                    guess = past + timedelta(days=365)
                if not today <= guess <= horizon:
                    continue
                if any(abs((d - guess).days) <= MATCH_DAYS for d, _ in announced):
                    continue
                if any(abs((date.fromisoformat(x["ex_date"]) - guess).days) <= MATCH_DAYS
                       for x in rows if date.fromisoformat(x["ex_date"]) > past + timedelta(days=MATCH_DAYS)
                       and date.fromisoformat(x["ex_date"]) < today):
                    continue                     # this year's payment already happened early
                upcoming.append(mk(guess, r["amount"], "expected", held_now, f"last year {past:%d %b %Y}"))

        # passed ex-dates while you held it, nothing recorded since
        for r in rows:
            d = date.fromisoformat(r["ex_date"])
            if not today - timedelta(days=RECORD_WINDOW_DAYS) <= d < today:
                continue
            shares = _held_on(txns, d, usdidr) if txns else 0.0
            if shares > 0 and not _recorded_since(txns, d):
                to_record.append(mk(d, r["amount"], "to_record", shares))
    upcoming.sort(key=lambda e: (e.ex_date, e.symbol))
    to_record.sort(key=lambda e: (e.ex_date, e.symbol))
    return {"upcoming": upcoming, "to_record": to_record}


def yearly_income(db, today: date | None = None) -> list[dict]:
    """Per holding: dividends per share over the last 12 months x shares held now, in IDR,
    and as a % of what you paid (yield on cost). A rough guide to a year's income."""
    today = today or date.today()
    usdidr = db.get_latest_fx("USDIDR") or 0.0
    since = (today - timedelta(days=365)).isoformat()
    out = []
    for sym, p in pf.replay(db.get_transactions(), usdidr).items():
        if not p.open:
            continue
        per_share = sum(r["amount"] or 0 for r in db.get_dividends(sym, since=since) if r["ex_date"] < today.isoformat())
        fx = 1.0 if p.currency == "IDR" else usdidr
        gross_idr = per_share * p.shares * fx
        out.append({"symbol": sym, "currency": p.currency, "per_share": per_share, "gross_idr": gross_idr,
                    "net_idr": gross_idr * (1 - TAX_PCT.get(p.currency, 0) / 100),
                    "yield_on_cost": gross_idr / p.cost_idr * 100 if p.cost_idr > 0 else None})
    return sorted(out, key=lambda r: -r["gross_idr"])

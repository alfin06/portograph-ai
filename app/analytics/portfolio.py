"""Your portfolio, worked out from the transactions ledger.

Average-cost method, the way IDX brokers report it: a buy adds its cost (fee included)
to the position; a sell takes out the average cost of the shares sold, and the
difference to what you got (after its fee) is realised profit. Dividends are counted
as income, separately.

Everything is in the stock's own currency, and also in IDR:
  - cost in IDR uses the USD/IDR rate on the day you bought,
  - value in IDR uses today's rate,
so for US holdings the IDR profit splits into "the price moved" and "the rupiah moved".

Research only: the numbers are as good as the transactions you enter.
"""
from dataclasses import dataclass, field

import pandas as pd

LOT = {"IDX": 100, "US": 1}
# Typical broker fees when you don't give one: IDX buy ~0.15%, sell ~0.25% (includes the
# 0.1% sales tax). US brokers for retail mostly charge nothing per trade.
DEFAULT_FEE_PCT = {("IDX", "BUY"): 0.15, ("IDX", "SELL"): 0.25, ("US", "BUY"): 0.0, ("US", "SELL"): 0.0}
EPS = 1e-9

# Concentration warnings: one stock above this share of the portfolio (ETFs are spread
# over many companies, so they are left out), or one sector/currency above this.
MAX_STOCK_PCT = 20.0
MAX_SECTOR_PCT = 40.0


class PortfolioError(ValueError):
    pass


@dataclass
class Position:
    symbol: str
    market: str
    currency: str
    asset_type: str
    shares: float = 0.0
    cost: float = 0.0              # open shares' cost, own currency, fees included
    cost_idr: float = 0.0          # same, at the rates on the buy days
    realised: float = 0.0          # own currency
    realised_idr: float = 0.0
    income: float = 0.0            # dividends, own currency
    income_idr: float = 0.0
    first_buy: str | None = None
    # filled in from prices
    close: float | None = None
    close_date: pd.Timestamp | None = None
    prev_close: float | None = None
    fx_now: float = 1.0
    sector: str | None = None
    weight: float | None = None    # % of the portfolio's value

    @property
    def open(self) -> bool:
        return self.shares > EPS

    @property
    def avg_cost(self) -> float | None:
        return self.cost / self.shares if self.open else None

    @property
    def value(self) -> float | None:
        return None if self.close is None else self.shares * self.close

    @property
    def value_idr(self) -> float | None:
        return None if self.value is None else self.value * self.fx_now

    @property
    def unrealised(self) -> float | None:
        return None if self.value is None else self.value - self.cost

    @property
    def unrealised_pct(self) -> float | None:
        return None if self.unrealised is None or self.cost <= 0 else self.unrealised / self.cost * 100

    @property
    def unrealised_idr(self) -> float | None:
        return None if self.value_idr is None else self.value_idr - self.cost_idr

    @property
    def unrealised_idr_pct(self) -> float | None:
        u = self.unrealised_idr
        return None if u is None or self.cost_idr <= 0 else u / self.cost_idr * 100

    @property
    def fx_effect_idr(self) -> float | None:
        """Part of the IDR profit that comes from the exchange rate, not the price."""
        if self.currency == "IDR" or self.unrealised_idr is None:
            return None
        return self.unrealised_idr - self.unrealised * self.fx_now

    @property
    def day_change_idr(self) -> float | None:
        if self.close is None or self.prev_close is None:
            return None
        return self.shares * (self.close - self.prev_close) * self.fx_now

    @property
    def day_change_pct(self) -> float | None:
        if self.close is None or not self.prev_close:
            return None
        return (self.close / self.prev_close - 1) * 100

    @property
    def lots(self) -> float:
        return self.shares / LOT.get(self.market, 1)


@dataclass
class Portfolio:
    positions: list[Position]                # open positions, largest first
    closed: list[Position]                   # fully sold, with realised profit
    untracked: list[str]                     # marked as holdings but with no transactions
    usdidr: float | None
    value_idr: float = 0.0
    cost_idr: float = 0.0
    unrealised_idr: float = 0.0
    realised_idr: float = 0.0
    income_idr: float = 0.0
    day_change_idr: float | None = None
    missing_prices: list[str] = field(default_factory=list)
    by_sector: list[tuple[str, float]] = field(default_factory=list)     # (name, % of value)
    by_currency: list[tuple[str, float]] = field(default_factory=list)
    by_market: list[tuple[str, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    flags: dict[str, str] = field(default_factory=dict)   # warning key ('stock:BBRI') -> text
    as_of: pd.Timestamp | None = None
    capital_idr: float | None = None

    @property
    def unrealised_pct(self) -> float | None:
        return self.unrealised_idr / self.cost_idr * 100 if self.cost_idr > 0 else None

    @property
    def day_change_pct(self) -> float | None:
        if self.day_change_idr is None:
            return None
        before = self.value_idr - self.day_change_idr
        return self.day_change_idr / before * 100 if before > 0 else None

    @property
    def invested_pct(self) -> float | None:
        return self.value_idr / self.capital_idr * 100 if self.capital_idr else None

    @property
    def total_return_idr(self) -> float:
        return self.unrealised_idr + self.realised_idr + self.income_idr


# --- the ledger -----------------------------------------------------------------------
def _fx(txn: dict, fallback: float | None) -> float:
    if txn.get("currency", "IDR") == "IDR":
        return 1.0
    rate = txn.get("fx_idr") or fallback
    if not rate:
        raise PortfolioError("no USD/IDR rate stored yet; run a US digest first")
    return rate


def replay(transactions: list[dict], usdidr: float | None = None) -> dict[str, Position]:
    """Positions from transactions (oldest first). Raises PortfolioError if a sell is
    larger than what was held on that day."""
    out: dict[str, Position] = {}
    for t in sorted(transactions, key=lambda t: (t["date"], t["id"] or 0)):
        sym = t["symbol"]
        p = out.get(sym)
        if p is None:
            p = out[sym] = Position(sym, t.get("market") or "IDX", t.get("currency") or "IDR",
                                    t.get("asset_type") or "STOCK")
        fx = _fx(t, usdidr)
        kind = t["kind"]
        if kind == "BUY":
            spent = t["shares"] * t["price"] + (t["fee"] or 0)
            p.shares += t["shares"]
            p.cost += spent
            p.cost_idr += spent * fx
            p.first_buy = p.first_buy or t["date"]
        elif kind == "SELL":
            if t["shares"] > p.shares + EPS:
                held = _qty(p.shares, p.market)
                raise PortfolioError(f"{sym}: sale on {t['date']} of {_qty(t['shares'], p.market)} "
                                     f"is more than the {held} held then")
            share = t["shares"] / p.shares
            removed, removed_idr = p.cost * share, p.cost_idr * share
            got = t["shares"] * t["price"] - (t["fee"] or 0)
            p.realised += got - removed
            p.realised_idr += got * fx - removed_idr
            p.shares -= t["shares"]
            p.cost -= removed
            p.cost_idr -= removed_idr
            if p.shares <= EPS:                      # closed: clear rounding dust
                p.shares = p.cost = p.cost_idr = 0.0
                p.first_buy = None
        elif kind == "DIV":
            p.income += t["amount"] or 0
            p.income_idr += (t["amount"] or 0) * fx
    return out


def _qty(shares: float, market: str) -> str:
    lot = LOT.get(market, 1)
    if lot > 1 and abs(shares / lot - round(shares / lot)) < EPS:
        return f"{shares / lot:,.0f} lots"
    return f"{shares:,.4g} shares" if shares != int(shares) else f"{shares:,.0f} shares"


def default_fee(market: str, kind: str, shares: float, price: float) -> float:
    return round(shares * price * DEFAULT_FEE_PCT.get((market, kind), 0.0) / 100, 2)


def check_ledger(db, extra: dict | None = None, without: int | None = None) -> None:
    """Raise PortfolioError if adding `extra` (or removing txn `without`) would make a
    sale larger than the holding on its day."""
    txns = [t for t in db.get_transactions() if t["id"] != without]
    if extra:
        txns.append({"id": 10 ** 12, **extra})
    replay(txns, db.get_latest_fx("USDIDR"))


def sync_holding_flags(db, symbol: str) -> None:
    """Keep instruments.is_holding in step with the ledger for symbols that have one."""
    txns = db.get_transactions(symbol)
    if txns:
        p = replay(txns, db.get_latest_fx("USDIDR")).get(symbol.upper())
        db.set_holding(symbol, bool(p and p.open))


# --- valuing it -------------------------------------------------------------------------
def _sector(db, p: Position) -> str:
    if p.asset_type == "ETF":
        prof = db.get_etf_profile(p.symbol)
        cat = (prof or {}).get("category")
        return f"ETF · {cat}" if cat else "ETF"
    f = db.get_latest_fundamentals(p.symbol)
    return (f or {}).get("sector") or "Unknown"


def _shares_of(items: list[tuple[str, float]], total: float) -> list[tuple[str, float]]:
    agg: dict[str, float] = {}
    for k, v in items:
        agg[k] = agg.get(k, 0.0) + v
    return sorted(((k, v / total * 100) for k, v in agg.items()), key=lambda kv: -kv[1])


def build(db) -> Portfolio:
    usdidr = db.get_latest_fx("USDIDR")
    txns = db.get_transactions()
    try:
        pos = replay(txns, usdidr)
    except PortfolioError as e:                 # only possible if the DB was edited by hand
        return Portfolio([], [], [], usdidr, warnings=[str(e)])
    tracked = set(pos)
    untracked = [i["symbol"] for i in db.get_instruments()
                 if i["is_holding"] and i["symbol"] not in tracked]

    open_, closed, missing = [], [], []
    for p in pos.values():
        if not p.open:
            if abs(p.realised) > EPS or abs(p.income) > EPS:
                closed.append(p)
            continue
        hist = db.get_price_history(p.symbol, days=5)
        if hist.empty:
            missing.append(p.symbol)
        else:
            p.close = float(hist["Close"].iloc[-1])
            p.close_date = hist.index[-1]
            p.prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
        p.fx_now = 1.0 if p.currency == "IDR" else (usdidr or 0.0)
        p.sector = _sector(db, p)
        open_.append(p)

    port = Portfolio([], closed, untracked, usdidr, missing_prices=missing)
    priced = [p for p in open_ if p.value_idr is not None]
    port.value_idr = sum(p.value_idr for p in priced)
    port.cost_idr = sum(p.cost_idr for p in priced)
    port.unrealised_idr = sum(p.unrealised_idr for p in priced)
    port.realised_idr = sum(p.realised_idr for p in pos.values())
    port.income_idr = sum(p.income_idr for p in pos.values())
    days = [p.day_change_idr for p in priced if p.day_change_idr is not None]
    port.day_change_idr = sum(days) if days else None
    port.as_of = max((p.close_date for p in priced), default=None)
    cap = db.get_setting("capital_idr")
    port.capital_idr = float(cap) if cap else None

    if port.value_idr > 0:
        for p in priced:
            p.weight = p.value_idr / port.value_idr * 100
        port.by_sector = _shares_of([(p.sector, p.value_idr) for p in priced], port.value_idr)
        port.by_currency = _shares_of([(p.currency, p.value_idr) for p in priced], port.value_idr)
        port.by_market = _shares_of([(p.market, p.value_idr) for p in priced], port.value_idr)
        for p in priced:
            if p.asset_type != "ETF" and p.weight > MAX_STOCK_PCT:
                port.flags[f"stock:{p.symbol}"] = (f"{p.symbol} is {p.weight:.1f}% of the portfolio "
                                                   f"(above {MAX_STOCK_PCT:.0f}% in one stock).")
        for name, w in port.by_sector:
            if not name.startswith("ETF") and name != "Unknown" and w > MAX_SECTOR_PCT:
                port.flags[f"sector:{name}"] = (f"{name} is {w:.1f}% of the portfolio "
                                                f"(above {MAX_SECTOR_PCT:.0f}% in one sector).")
        port.warnings.extend(port.flags.values())
    port.positions = sorted(open_, key=lambda p: -(p.value_idr or 0))
    port.closed.sort(key=lambda p: p.symbol)
    return port


# --- daily digest ---------------------------------------------------------------------
MOVE_ALERT_PCT = 5.0          # a holding moving this much in one session gets its own line
FLAGS_SETTING = "portfolio_flags"


@dataclass
class DigestSection:
    value_idr: float
    unrealised_idr: float
    unrealised_pct: float | None
    market_change_idr: float | None       # this market's holdings, this session
    market_change_pct: float | None
    big_moves: list[tuple[str, float, float]]    # (symbol, % move, IDR change)
    new_flags: list[str]
    still_flagged: int
    cleared: list[str]                    # keys like 'stock:BBRI', shown as names
    week_change_idr: float | None = None  # vs the snapshot about a week earlier
    week_change_pct: float | None = None


def record_snapshot(db, port: Portfolio) -> str | None:
    """Store today's value; the date is the newest close used. Returns that date."""
    if not port.positions or port.as_of is None:
        return None
    day = f"{port.as_of:%Y-%m-%d}"
    db.upsert_portfolio_day(day, port.value_idr, port.cost_idr, port.unrealised_idr,
                            port.realised_idr, port.income_idr, port.usdidr)
    return day


def _week_change(db, port: Portfolio):
    hist = db.get_portfolio_history()
    if port.as_of is None or not hist:
        return None, None
    cutoff = f"{port.as_of - pd.Timedelta(days=7):%Y-%m-%d}"
    before = [h for h in hist if h["date"] <= cutoff]
    if not before:
        return None, None
    then = before[-1]
    # take out money put in or taken out since then, so a new buy isn't counted as a gain
    gain = (port.value_idr - port.cost_idr + port.realised_idr + port.income_idr) - \
           (then["value_idr"] - then["cost_idr"] + then["realised_idr"] + then["income_idr"])
    return gain, (gain / then["value_idr"] * 100 if then["value_idr"] > 0 else None)


def digest_section(db, market: str, session_date: pd.Timestamp, record: bool = True) -> DigestSection | None:
    """The portfolio part of a market digest. With record=True (real digests, not dry runs)
    the day's value is stored and the warning list remembered, so the next digest only
    reports warnings that are new or cleared."""
    import json

    port = build(db)
    if not port.positions:
        return None
    if record:
        record_snapshot(db, port)
    mine = [p for p in port.positions if p.market == market and p.close_date == session_date
            and p.day_change_idr is not None]
    change = sum(p.day_change_idr for p in mine) if mine else None
    before = sum(p.value_idr - p.day_change_idr for p in mine) if mine else 0
    moves = sorted(((p.symbol, p.day_change_pct, p.day_change_idr) for p in mine
                    if p.day_change_pct is not None and abs(p.day_change_pct) >= MOVE_ALERT_PCT),
                   key=lambda m: -abs(m[1]))

    try:
        seen = set(json.loads(db.get_setting(FLAGS_SETTING, "[]")))
    except (ValueError, TypeError):
        seen = set()
    now = set(port.flags)
    if record:
        db.set_setting(FLAGS_SETTING, json.dumps(sorted(now)))
    week, week_pct = _week_change(db, port)
    return DigestSection(
        value_idr=port.value_idr, unrealised_idr=port.unrealised_idr, unrealised_pct=port.unrealised_pct,
        market_change_idr=change, market_change_pct=change / before * 100 if mine and before > 0 else None,
        big_moves=moves, new_flags=[port.flags[k] for k in sorted(now - seen)],
        still_flagged=len(now & seen), cleared=[k.split(":", 1)[1] for k in sorted(seen - now)],
        week_change_idr=week, week_change_pct=week_pct)

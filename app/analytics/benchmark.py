"""Your portfolio against the market, all in rupiah.

Two views:
- current mix: what the holdings you own now, in today's quantities, did over past periods,
  next to the IHSG, the S&P 500 and a blend of the two in your IDX/US split. It works from
  day one but is hindsight: it ignores when you actually bought and sold.
- track record: your real result from the value history the digests store, with buys,
  sells and dividends taken out (a time-weighted return), against the benchmarks over the
  same dates. It only means something after a few weeks.

Prices are Yahoo's dividend-adjusted closes, so holdings and the S&P 500 (SPY) include
dividends. The IHSG index (^JKSE) does not, which flatters IDX holdings by roughly the
index's dividend yield, about 2-3% a year. Both indexes are stored by the mood score
(app.analytics.sentiment) with each digest."""
from dataclasses import dataclass, field

import pandas as pd

from app.analytics.fx_returns import period_start

# key -> (stored symbol, currency, label)
BENCHMARKS = {
    "ihsg": ("^JKSE", "IDR", "IHSG"),
    "sp500": ("SPY", "USD", "S&P 500"),
}
PERIODS = ["1m", "3m", "6m", "ytd", "1y"]
CHART_PERIOD = "1y"
MIN_TRACK_DAYS = 2      # history rows needed for a track record at all


@dataclass
class PeriodRow:
    period: str
    start: pd.Timestamp
    mine: float           # percent, in IDR
    bench: dict[str, float]          # key -> percent ('blend' included)

    @property
    def vs_blend(self) -> float | None:
        b = self.bench.get("blend")
        return None if b is None else self.mine - b


@dataclass
class TrackRecord:
    start: str
    end: str
    days: int             # snapshots used
    mine: float           # time-weighted percent
    bench: dict[str, float]


@dataclass
class Benchmark:
    end: pd.Timestamp | None
    idx_weight: float                 # share of IDX holdings in the blend, percent
    rows: list[PeriodRow] = field(default_factory=list)
    growth: dict[str, pd.Series] = field(default_factory=dict)   # label -> 100 at the chart start
    track: TrackRecord | None = None
    missing: list[str] = field(default_factory=list)              # symbols without prices

    @property
    def labels(self) -> dict[str, str]:
        return {"mine": "Your mix", **{k: v[2] for k, v in BENCHMARKS.items()}, "blend": "Blend"}


def _to_idr(close: pd.Series, currency: str, usdidr: pd.Series) -> pd.Series:
    if currency == "IDR":
        return close
    fx = usdidr.reindex(close.index.union(usdidr.index)).sort_index().ffill().reindex(close.index)
    return (close * fx).dropna()


def _frame(columns: dict[str, pd.Series]) -> pd.DataFrame:
    """Columns on one calendar, gaps (holidays differ per market) filled forward, ending on
    the earliest last date so every column covers the same window."""
    cols = {k: s.dropna() for k, s in columns.items() if s is not None and not s.dropna().empty}
    if not cols:
        return pd.DataFrame()
    end = min(s.index[-1] for s in cols.values())
    df = pd.DataFrame(cols).sort_index().ffill()
    return df[df.index <= end]


def blend(ihsg: pd.Series, sp500: pd.Series, idx_weight: float) -> pd.Series:
    """Rebalanced daily: each day's return is the weighted mix of the two indexes' returns."""
    df = pd.DataFrame({"i": ihsg, "s": sp500}).dropna()
    if df.empty:
        return pd.Series(dtype=float)
    r = df.pct_change().fillna(0.0)
    w = idx_weight / 100
    return (1 + w * r["i"] + (1 - w) * r["s"]).cumprod() * 100


def mix_value(positions, prices: dict[str, pd.Series], usdidr: pd.Series) -> tuple[pd.Series, list[str]]:
    """Value in IDR of today's holdings, held unchanged, on each past day. A holding without
    stored prices is left out and reported."""
    parts, missing = {}, []
    for p in positions:
        close = prices.get(p.symbol)
        if close is None or close.dropna().empty:
            missing.append(p.symbol)
            continue
        parts[p.symbol] = _to_idr(close, p.currency, usdidr) * p.shares
    if not parts:
        return pd.Series(dtype=float), missing
    df = pd.DataFrame(parts).sort_index().ffill().dropna()   # from the day every holding has a price
    return df.sum(axis=1), missing


def _ret(s: pd.Series, start: pd.Timestamp) -> float | None:
    before = s[s.index <= start].dropna()
    if before.empty or not before.iloc[-1]:
        return None
    return float((s.dropna().iloc[-1] / before.iloc[-1] - 1) * 100)


def period_rows(df: pd.DataFrame) -> list[PeriodRow]:
    """One row per period for which every column has a value at the start."""
    rows = []
    if df.empty or "mine" not in df:
        return rows
    end = df.index[-1]
    for period in PERIODS:
        start = period_start(end, period)
        if df.index[0] > start:
            continue
        mine = _ret(df["mine"], start)
        bench = {k: _ret(df[k], start) for k in df.columns if k != "mine"}
        if mine is None or any(v is None for v in bench.values()):
            continue
        rows.append(PeriodRow(period, df[df.index <= start].index[-1], mine, bench))
    return rows


def track_record(history: list[dict], bench: pd.DataFrame) -> TrackRecord | None:
    """Time-weighted return from the stored snapshots: each day's gain (value change less
    the money put in or taken out, which shows up as a change in cost, realised profit and
    dividends) over the value the day before, chained."""
    if len(history) < MIN_TRACK_DAYS:
        return None
    growth = 1.0
    for prev, cur in zip(history, history[1:]):
        if prev["value_idr"] <= 0:
            continue
        gain = (cur["value_idr"] - cur["cost_idr"] + cur["realised_idr"] + cur["income_idr"]) - \
               (prev["value_idr"] - prev["cost_idr"] + prev["realised_idr"] + prev["income_idr"])
        growth *= 1 + gain / prev["value_idr"]
    start, end = pd.Timestamp(history[0]["date"]), pd.Timestamp(history[-1]["date"])
    b = {}
    for k in bench.columns:
        s = bench[k].dropna()
        s0, s1 = s[s.index <= start], s[s.index <= end]
        if not s0.empty and not s1.empty and s0.iloc[-1]:
            b[k] = float((s1.iloc[-1] / s0.iloc[-1] - 1) * 100)
    return TrackRecord(history[0]["date"], history[-1]["date"], len(history), float((growth - 1) * 100), b)


def compute(positions, prices: dict[str, pd.Series], usdidr: pd.Series, history: list[dict],
            idx_weight: float) -> Benchmark:
    """positions: open Position objects; prices: symbol -> close for holdings and the
    benchmark symbols; idx_weight: IDX share of the portfolio's value, percent."""
    mine, missing = mix_value(positions, prices, usdidr)
    idx = {k: _to_idr(prices[sym], ccy, usdidr) for k, (sym, ccy, _) in BENCHMARKS.items()
           if prices.get(sym) is not None and not prices[sym].dropna().empty}
    missing += [BENCHMARKS[k][0] for k in BENCHMARKS if k not in idx]
    cols = {"mine": mine, **idx}
    if len(idx) == len(BENCHMARKS):
        cols["blend"] = blend(idx["ihsg"], idx["sp500"], idx_weight)
    df = _frame(cols)
    out = Benchmark(end=df.index[-1] if not df.empty else None, idx_weight=idx_weight, missing=missing)
    out.rows = period_rows(df)
    if not df.empty:
        start = period_start(df.index[-1], CHART_PERIOD)
        window = df[df.index >= df[df.index <= start].index[-1]] if df.index[0] <= start else df
        window = window.dropna(axis=1, how="all").dropna()
        if not window.empty:
            out.growth = {out.labels[k]: window[k] / window[k].iloc[0] * 100 for k in window.columns}
    bench_only = _frame({k: v for k, v in cols.items() if k != "mine"})
    out.track = track_record(history, bench_only) if not bench_only.empty else None
    return out


# --- DB glue ---------------------------------------------------------------------------
def from_db(db, port=None) -> Benchmark | None:
    """None when nothing is held."""
    from app.analytics import portfolio as pf
    port = port or pf.build(db)
    if not port.positions:
        return None
    syms = [p.symbol for p in port.positions] + [sym for sym, _, _ in BENCHMARKS.values()]
    prices = {}
    for s in syms:
        h = db.get_price_history(s)
        if not h.empty:
            prices[s] = h["Close"]
    idx_weight = dict(port.by_market).get("IDX", 0.0)
    return compute(port.positions, prices, db.get_fx_history("USDIDR"),
                   db.get_portfolio_history(), idx_weight)

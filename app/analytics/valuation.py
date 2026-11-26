"""Valuation bands: is a stock cheap or expensive versus its own history?

For every trading day of the last ~5 years we compute P/E, P/B and dividend yield from
the unadjusted close and the figures the company had published by then:
  - book value per share = equity / shares (annual and quarterly balance sheets)
  - EPS = net income / shares, trailing 12 months (annual, or 4 consecutive quarters)
  - trailing 12-month dividends per share
Figures count from REPORT_LAG days after the period ends (when they're public), and are
converted to the trading currency with USD/IDR on that date when the company reports in
another currency (e.g. ITMG reports in USD, trades in IDR).

Each metric's daily values become a distribution (5% steps), so today's value gets a
percentile rank and we can say which price matches the stock's usual levels.
"""
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

REPORT_LAG = {"annual": 90, "quarterly": 45}   # days from period end to publication
QS = list(range(0, 101, 5))
MIN_DAYS = 250                                  # need about a year of daily values
DIV_GAP_DAYS = 45                               # trading days a late yearly dividend may lag
MAX_AGE_DAYS = 7
METRICS = ("pb", "pe", "yield")
LABEL = {"pb": "P/B", "pe": "P/E", "yield": "Yield"}


# --- fundamentals timeline -----------------------------------------------------------
def _fx_factor(from_ccy: str, to_ccy: str, fx: pd.Series | None, when: pd.Timestamp) -> float | None:
    if from_ccy == to_ccy or not from_ccy:
        return 1.0
    if fx is None or fx.dropna().empty:
        return None
    s = fx.dropna()
    s = s[s.index <= when]
    if s.empty:
        return None
    rate = float(s.iloc[-1])
    if (from_ccy, to_ccy) == ("USD", "IDR"):
        return rate
    if (from_ccy, to_ccy) == ("IDR", "USD"):
        return 1 / rate
    return None


def _shares_near(shares: dict[str, float], when: str) -> float | None:
    """Share count at a period end, else the closest one reported."""
    if when in shares:
        return shares[when]
    if not shares:
        return None
    t = pd.Timestamp(when)
    best = min(shares, key=lambda d: abs(pd.Timestamp(d) - t))
    return shares[best]


def _ttm_income(quarterly_income: dict[str, float], end: str) -> float | None:
    """Sum of 4 quarters ending at `end`, if all 4 are reported."""
    t = pd.Timestamp(end)
    window = [v for d, v in quarterly_income.items() if t - timedelta(days=340) < pd.Timestamp(d) <= t]
    return sum(window) if len(window) == 4 else None


def timeline(inputs: dict, fx: pd.Series | None = None) -> pd.DataFrame:
    """Per-share book value and TTM EPS in the trading currency, indexed by the date
    each figure became public. Columns may be NaN where a figure wasn't reported."""
    fin, trade = inputs["financial_currency"], inputs["currency"]
    a, q = inputs["annual"], inputs["quarterly"]
    all_shares = {**a["shares"], **q["shares"]}
    points: dict[str, dict] = {}
    for kind, src in (("annual", a), ("quarterly", q)):
        for end, equity in src["equity"].items():
            sh = _shares_near(all_shares, end)
            if sh:
                p = points.setdefault(end, {"lag": REPORT_LAG[kind]})
                p["lag"] = min(p["lag"], REPORT_LAG[kind])
                p["bvps"] = equity / sh
        incomes = src["income"].items() if kind == "annual" else \
            ((end, _ttm_income(src["income"], end)) for end in src["income"])
        for end, income in incomes:
            sh = _shares_near(all_shares, end)
            if income is not None and sh:
                p = points.setdefault(end, {"lag": REPORT_LAG[kind]})
                p["lag"] = min(p["lag"], REPORT_LAG[kind])
                p["eps"] = income / sh

    rows = {}
    for end, p in points.items():
        public = pd.Timestamp(end) + timedelta(days=p["lag"])
        k = _fx_factor(fin, trade, fx, public)
        if k is None:
            continue
        rows[public] = {"bvps": p.get("bvps", np.nan) * k, "eps": p.get("eps", np.nan) * k,
                        "period_end": end}
    if not rows:
        return pd.DataFrame(columns=["bvps", "eps", "period_end"])
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def latest_per_share(inputs: dict) -> tuple[float | None, float | None]:
    """(book value, TTM EPS) per share from the newest period reported, in the
    company's reporting currency."""
    a, q = inputs["annual"], inputs["quarterly"]
    all_shares = {**a["shares"], **q["shares"]}
    equity = {**a["equity"], **q["equity"]}
    bvps = None
    if equity:
        end = max(equity)
        sh = _shares_near(all_shares, end)
        bvps = equity[end] / sh if sh else None
    earnings = dict(a["income"])
    for end in q["income"]:
        ttm = _ttm_income(q["income"], end)
        if ttm is not None:
            earnings[end] = ttm
    eps = None
    if earnings:
        end = max(earnings)
        sh = _shares_near(all_shares, end)
        eps = earnings[end] / sh if sh else None
    return bvps, eps


def _ttm_dividends(divs: pd.Series, idx: pd.DatetimeIndex) -> pd.Series:
    if divs is None or divs.empty:
        return pd.Series(0.0, index=idx)
    cs = divs.sort_index().cumsum()
    now = cs.reindex(cs.index.union(idx)).ffill().reindex(idx).fillna(0)
    back = idx - pd.Timedelta(days=365)
    then = cs.reindex(cs.index.union(back)).ffill().reindex(back).fillna(0)
    ttm = pd.Series(now.values - then.values, index=idx)
    # A yearly payer whose payment lands a few days later than last year's would show a
    # zero trailing dividend in between; carry the last amount over such short gaps
    return ttm.mask(ttm <= 0).ffill(limit=DIV_GAP_DAYS).fillna(0)


def daily_metrics(close: pd.Series, tl: pd.DataFrame, divs: pd.Series) -> pd.DataFrame:
    idx = close.index
    out = pd.DataFrame(index=idx)
    for col in ("bvps", "eps"):
        s = tl[col].dropna() if col in tl else pd.Series(dtype=float)
        if s.empty:
            out[col] = np.nan
        else:
            out[col] = s.reindex(s.index.union(idx)).ffill().reindex(idx)
    out["pb"] = (close / out["bvps"]).where(out["bvps"] > 0)
    out["pe"] = (close / out["eps"]).where(out["eps"] > 0)
    ttm = _ttm_dividends(divs, idx)
    out["yield"] = (ttm / close * 100) if ttm.gt(0).any() else np.nan
    return out


# --- bands -----------------------------------------------------------------------------
def band(s: pd.Series) -> dict | None:
    s = s.dropna()
    if len(s) < MIN_DAYS:
        return None
    return {"q": [float(v) for v in np.percentile(s.values, QS)], "days": int(len(s)),
            "start": f"{s.index[0]:%Y-%m-%d}", "end": f"{s.index[-1]:%Y-%m-%d}"}


def rank(b: dict, value: float) -> float:
    """Percent of past days with a lower value (0-100)."""
    q = np.asarray(b["q"])
    if value <= q[0]:
        return 0.0
    if value >= q[-1]:
        return 100.0
    return float(np.interp(value, q, QS))


def at(b: dict, pct: float) -> float:
    return float(np.interp(pct, QS, b["q"]))


def roe_history(inputs: dict) -> list[dict]:
    """Yearly ROE (net income / year-end equity), plus trailing-12-month on the latest quarter."""
    a, q = inputs["annual"], inputs["quarterly"]
    out = [{"period": end[:4], "roe": a["income"][end] / eq * 100}
           for end, eq in sorted(a["equity"].items()) if end in a["income"] and eq > 0]
    if q["equity"]:
        last = max(q["equity"])
        ttm = _ttm_income(q["income"], last)
        if ttm is not None and q["equity"][last] > 0 and last not in a["equity"]:
            out.append({"period": f"TTM {last[:7]}", "roe": ttm / q["equity"][last] * 100})
    return out


def build_profile(inputs: dict, fx: pd.Series | None = None, today: date | None = None) -> dict | None:
    close = inputs["close"]
    tl = timeline(inputs, fx)
    m = daily_metrics(close, tl, inputs["dividends"])
    bands = {k: band(m[k]) for k in METRICS}
    if not any(bands.values()):
        return None
    today = pd.Timestamp(today or date.today())
    # Current per-share figures: the latest reported (even if very recent), at today's rate
    bvps_fin, eps_fin = latest_per_share(inputs)
    k = _fx_factor(inputs["financial_currency"], inputs["currency"], fx, today)
    cur_bvps = None if bvps_fin is None or k is None else bvps_fin * k
    cur_eps = None if eps_fin is None or k is None else eps_fin * k
    divs = inputs["dividends"]
    ttm_div = float(divs[divs.index > today - pd.Timedelta(days=365)].sum()) if not divs.empty else 0.0
    years = max((pd.Timestamp(b["end"]) - pd.Timestamp(b["start"])).days / 365.25
                for b in bands.values() if b)
    return {
        "currency": inputs["currency"], "financial_currency": inputs["financial_currency"],
        "bands": bands, "years": round(years, 1),
        "current": {"bvps": cur_bvps, "eps": cur_eps, "ttm_div": ttm_div},
        "roe": roe_history(inputs),
    }


# --- reading the bands -----------------------------------------------------------------
@dataclass
class Reading:
    metric: str
    value: float
    rank: float
    label: str
    cheap_side: str          # 'cheap' | 'expensive' | 'normal'
    low: float               # 10th percentile
    typical: float           # median
    high: float              # 90th percentile
    price_cheap: float           # price at which this metric sits at its cheap 25% boundary
    price_typical: float         # price at its median
    price_deep: float            # price at its cheapest 10% boundary


def assess(profile: dict, close: float) -> list[Reading]:
    cur, out = profile["current"], []
    for k in METRICS:
        b = profile["bands"].get(k)
        if not b:
            continue
        if k == "yield":
            d = cur.get("ttm_div") or 0
            if d <= 0:
                continue
            v = d / close * 100
            r = rank(b, v)
            side = "cheap" if r >= 75 else "expensive" if r <= 25 else "normal"
            label = {"cheap": "high vs its history (cheap side)", "expensive": "low vs its history (expensive side)",
                     "normal": "around its usual level"}[side]
            out.append(Reading(k, v, r, label, side, at(b, 10), at(b, 50), at(b, 90),
                               d / (at(b, 75) / 100), d / (at(b, 50) / 100), d / (at(b, 90) / 100)))
        else:
            per_share = cur.get("bvps" if k == "pb" else "eps")
            if not per_share or per_share <= 0:
                continue
            v = close / per_share
            r = rank(b, v)
            side = "cheap" if r <= 25 else "expensive" if r >= 75 else "normal"
            label = {"cheap": "cheap vs its history", "expensive": "expensive vs its history",
                     "normal": "around its usual level"}[side]
            out.append(Reading(k, v, r, label, side, at(b, 10), at(b, 50), at(b, 90),
                               at(b, 25) * per_share, at(b, 50) * per_share, at(b, 10) * per_share))
    return out


def roe_warning(roe: list[dict]) -> str | None:
    """Flag a falling ROE: a stock can look cheap because the business is getting worse."""
    vals = [r["roe"] for r in roe]
    if len(vals) < 3:
        return None
    latest, before = vals[-1], vals[:-1]
    avg = sum(before) / len(before)
    if latest < avg - 3:
        return (f"ROE has fallen to {latest:.1f}% from a {avg:.1f}% average: cheap multiples may reflect "
                f"a weaker business rather than a bargain.")
    return None


# --- cache -----------------------------------------------------------------------------
def is_stale(profile: dict | None, today: date | None = None) -> bool:
    if profile is None:
        return True
    today = today or date.today()
    return profile["as_of"] <= (today - timedelta(days=MAX_AGE_DAYS)).isoformat()


def get_profile(db, inst, provider=None, fx_provider=None, today: date | None = None,
                force: bool = False) -> dict | None:
    """Stored bands if fresh, otherwise recompute (a few seconds of Yahoo calls) and store.
    Falls back to a stale copy if Yahoo fails. force=True rebuilds even a fresh copy."""
    cached = db.get_valuation_bands(inst.symbol)
    if not force and not is_stale(cached, today):
        return cached
    if provider is None:
        from app.providers.yahoo_valuation import YahooValuation
        provider = YahooValuation()
    inputs = provider.get_inputs(inst.yahoo)
    if inputs is None:
        return cached
    fx = None
    if inputs["financial_currency"] and inputs["financial_currency"] != inputs["currency"]:
        fx = db.get_fx_history("USDIDR")
        if fx.empty or fx.index[0] > inputs["close"].index[0] - pd.Timedelta(days=200):
            if fx_provider is None:
                from app.providers.yahoo import YahooProvider
                fx_provider = YahooProvider()
            more = fx_provider.get_fx("USDIDR", period="10y")
            if more is not None and not more.empty:
                db.upsert_fx("USDIDR", more)
                fx = db.get_fx_history("USDIDR")
    profile = build_profile(inputs, fx, today)
    if profile is None:
        return cached
    as_of = (today or date.today()).isoformat()
    db.upsert_valuation_bands(inst.symbol, as_of, provider.name, profile)
    return {**profile, "as_of": as_of}

"""Returns in IDR and in USD, with the currency effect split out.

USD/IDR up means the rupiah weakened. For a USD asset held by an IDR investor:
    (1 + return in IDR) = (1 + return in USD) * (1 + USD/IDR change)
so a weaker rupiah adds to US returns and subtracts from IDX returns measured in USD.
Prices are Yahoo's dividend-adjusted closes, so returns include reinvested dividends."""
from dataclasses import dataclass

import pandas as pd

PERIODS = {
    "1m": pd.DateOffset(months=1), "3m": pd.DateOffset(months=3), "6m": pd.DateOffset(months=6),
    "1y": pd.DateOffset(years=1), "2y": pd.DateOffset(years=2),
}
PERIOD_NAMES = [*PERIODS, "ytd"]


@dataclass
class CurrencyReturn:
    symbol: str
    currency: str
    start: pd.Timestamp
    end: pd.Timestamp
    local: float          # in the asset's own currency, percent
    idr: float
    usd: float
    fx: float             # USD/IDR change over the same dates, percent (+ = rupiah weaker)


def period_start(end: pd.Timestamp, period: str) -> pd.Timestamp:
    if period == "ytd":
        return pd.Timestamp(year=end.year - 1, month=12, day=31)
    return end - PERIODS[period]


def _asof(series: pd.Series, when: pd.Timestamp):
    """Last value on or before `when`, with its date; None if the series starts later."""
    s = series.dropna()
    s = s[s.index <= when]
    return (None, None) if s.empty else (s.index[-1], float(s.iloc[-1]))


def currency_return(symbol: str, currency: str, close: pd.Series, usdidr: pd.Series,
                    period: str = "1y") -> CurrencyReturn | None:
    if close is None or close.dropna().empty or usdidr is None or usdidr.dropna().empty:
        return None
    end_date = close.dropna().index[-1]
    start_target = period_start(end_date, period)
    if close.dropna().index[0] > start_target:
        return None  # not enough history for this period
    start_date, p0 = _asof(close, start_target)
    p1 = float(close.dropna().iloc[-1])
    # FX on the calendar dates (not each market's last trading day), so every asset
    # compared over the same window gets the same currency move
    _, fx0 = _asof(usdidr, start_target)
    _, fx1 = _asof(usdidr, end_date)
    if not p0 or fx0 is None or fx1 is None:
        return None

    local = p1 / p0 - 1
    fx = fx1 / fx0 - 1
    if currency == "USD":
        usd, idr = local, (1 + local) * (1 + fx) - 1
    else:
        idr, usd = local, (1 + local) / (1 + fx) - 1
    return CurrencyReturn(symbol, currency, start_date, end_date,
                          local * 100, idr * 100, usd * 100, fx * 100)


@dataclass
class Comparison:
    results: list[CurrencyReturn]
    problems: list[str]
    growth: dict[str, dict[str, pd.Series]]   # symbol -> {"local"|"idr"|"usd": value of 100 over time}


def _in(close: pd.Series, currency: str, target: str, usdidr: pd.Series) -> pd.Series:
    """Prices converted to `target` currency ('local' leaves them alone)."""
    if target == "local" or target == currency.lower():
        return close
    fx = usdidr.reindex(close.index.union(usdidr.index)).sort_index().ffill().reindex(close.index)
    return close * fx if target == "idr" else close / fx


def compare_closes(found: list[tuple[str, str, pd.Series]], usdidr: pd.Series,
                   period: str = "1y") -> Comparison:
    """Returns for (symbol, currency, close) rows over one common window: IDX and US close
    on different days, so every row ends on the earliest last date and shares one FX move."""
    results, problems, growth = [], [], {}
    common_end = min((c.index[-1] for _, _, c in found if not c.empty), default=None)
    for sym, ccy, close in found:
        close = close[close.index <= common_end].dropna()
        r = currency_return(sym, ccy, close, usdidr, period)
        if r is None:
            problems.append(f"{sym}: not enough history for {period}")
            continue
        results.append(r)
        window = close[close.index >= r.start]
        growth[sym] = {}
        for target in ("local", "idr", "usd"):
            s = _in(window, ccy, target, usdidr).dropna()
            growth[sym][target] = s / s.iloc[0] * 100 if not s.empty and s.iloc[0] else s
    return Comparison(results, problems, growth)

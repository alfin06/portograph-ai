"""Inputs for valuation bands from Yahoo: unadjusted daily closes, dividends, and the
reported equity, net income and share count from annual and quarterly statements.

Unadjusted closes matter: Yahoo's adjusted closes are lowered for every later dividend,
which would make past P/E and P/B look cheaper than they were (ITMG's 2021 adjusted
close is less than half the real one).
"""
import logging
import math

import pandas as pd

log = logging.getLogger(__name__)

SOURCE = "yahoo"
EQUITY_ROWS = ("Common Stock Equity", "Stockholders Equity")
SHARES_ROWS = ("Ordinary Shares Number", "Share Issued")
INCOME_ROWS = ("Net Income Common Stockholders", "Net Income")


def _naive(idx) -> pd.DatetimeIndex:
    idx = pd.to_datetime(idx)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def _row(df: pd.DataFrame | None, names) -> dict[str, float]:
    """{YYYY-MM-DD period end: value} for the first row name present, NaNs dropped."""
    if df is None or df.empty:
        return {}
    for n in names:
        if n in df.index:
            out = {}
            for col, v in df.loc[n].items():
                try:
                    f = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isnan(f):
                    out[pd.Timestamp(col).strftime("%Y-%m-%d")] = f
            return out
    return {}


def fetch_raw(yahoo_symbol: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(yahoo_symbol)
    info = t.info or {}
    hist = t.history(period="5y", auto_adjust=False)
    return {
        "currency": (info.get("currency") or "").upper(),
        "financial_currency": (info.get("financialCurrency") or info.get("currency") or "").upper(),
        "close": hist["Close"] if not hist.empty else pd.Series(dtype=float),
        "dividends": t.dividends,
        "annual_bs": t.balance_sheet, "annual_is": t.income_stmt,
        "quarterly_bs": t.quarterly_balance_sheet, "quarterly_is": t.quarterly_income_stmt,
    }


def normalize(raw: dict | None) -> dict | None:
    """Plain, JSON-friendly inputs; None if there are no prices."""
    if not raw:
        return None
    close = raw.get("close")
    if close is None or close.dropna().empty:
        return None
    close = close.dropna()
    close.index = _naive(close.index)
    divs = raw.get("dividends")
    if divs is None:
        divs = pd.Series(dtype=float)
    divs = divs[divs > 0]
    divs.index = _naive(divs.index)
    return {
        "currency": raw.get("currency") or "",
        "financial_currency": raw.get("financial_currency") or raw.get("currency") or "",
        "close": close[~close.index.duplicated(keep="last")],
        "dividends": divs.groupby(level=0).sum(),
        "annual": {"equity": _row(raw.get("annual_bs"), EQUITY_ROWS),
                   "shares": _row(raw.get("annual_bs"), SHARES_ROWS),
                   "income": _row(raw.get("annual_is"), INCOME_ROWS)},
        "quarterly": {"equity": _row(raw.get("quarterly_bs"), EQUITY_ROWS),
                      "shares": _row(raw.get("quarterly_bs"), SHARES_ROWS),
                      "income": _row(raw.get("quarterly_is"), INCOME_ROWS)},
    }


class YahooValuation:
    name = SOURCE

    def __init__(self, fetcher=None):
        self._fetch = fetcher or fetch_raw

    def get_inputs(self, yahoo_symbol: str) -> dict | None:
        try:
            return normalize(self._fetch(yahoo_symbol))
        except Exception as e:  # network, Yahoo format change
            log.warning("Valuation inputs for %s failed: %s", yahoo_symbol, e)
            return None

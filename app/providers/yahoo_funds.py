"""ETF profile from Yahoo: expense ratio, AUM, category, yield, sector weights, top holdings.

Yahoo only publishes the top 10 holdings. Full holdings files exist at some issuers
but most block automated downloads (iShares, Invesco) or have no public file (Vanguard),
so overlap analysis works from the top 10 plus sector weights plus price correlation."""
import logging
import math
from datetime import datetime, timezone

log = logging.getLogger(__name__)

SOURCE = "yahoo"


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def fetch_raw(symbol: str) -> dict | None:
    """Pull everything we need from yfinance into a plain dict (the only network part)."""
    import yfinance as yf
    t = yf.Ticker(symbol)
    info = t.info or {}
    if info.get("quoteType") != "ETF":
        return {"info": info}
    fd = t.funds_data
    ops = {}
    try:
        df = fd.fund_operations
        if df is not None and symbol in df.columns:
            ops = df[symbol].to_dict()
    except Exception:
        pass
    top = []
    try:
        th = fd.top_holdings
        for sym, row in th.iterrows():
            top.append((str(sym), str(row.get("Name", "")), float(row["Holding Percent"])))
    except Exception:
        pass
    try:
        sectors = dict(fd.sector_weightings or {})
    except Exception:
        sectors = {}
    return {"info": info, "ops": ops, "top": top, "sectors": sectors}


def normalize_fund(raw: dict | None) -> dict | None:
    """Raw Yahoo pieces -> our profile. Percent fields in percent (0.03 = 0.03%)."""
    if not raw or raw.get("info", {}).get("quoteType") != "ETF":
        return None
    info, ops = raw["info"], raw.get("ops") or {}

    expense = _num(ops.get("Annual Report Expense Ratio"))
    expense = expense * 100 if expense is not None else _num(info.get("netExpenseRatio"))
    turnover = _num(ops.get("Annual Holdings Turnover"))
    yld = _num(info.get("yield"))
    inception = _num(info.get("fundInceptionDate"))
    return {
        "name": info.get("longName") or info.get("shortName"),
        "family": info.get("fundFamily"),
        "category": info.get("category"),
        "expense_ratio": expense,
        "aum_usd": _num(info.get("totalAssets")),
        "yield": None if yld is None else yld * 100,
        "turnover": None if turnover is None else turnover * 100,
        "inception": None if inception is None else
            datetime.fromtimestamp(inception, tz=timezone.utc).date().isoformat(),
        "sectors": {k: v * 100 for k, v in (raw.get("sectors") or {}).items() if _num(v) is not None},
        "top_holdings": [{"symbol": s.upper(), "name": n, "weight": w * 100}
                         for s, n, w in raw.get("top") or []],
    }


class YahooFunds:
    name = SOURCE

    def __init__(self, fetcher=None):
        self._fetch = fetcher or fetch_raw

    def get_profile(self, symbol: str) -> dict | None:
        try:
            return normalize_fund(self._fetch(symbol.upper()))
        except Exception as e:
            log.warning("ETF profile failed for %s: %s", symbol, e)
            return None

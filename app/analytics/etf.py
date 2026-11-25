"""ETF comparison: holdings overlap, sector overlap, return correlation and fee cost."""
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

PROFILE_MAX_AGE_DAYS = 7


@dataclass
class Overlap:
    holdings_pct: float            # sum over shared top holdings of the smaller weight
    coverage_a: float              # % of fund A that its listed top holdings represent
    coverage_b: float
    shared: list[tuple[str, float, float]] = field(default_factory=list)  # (symbol, w_a, w_b)
    sector_pct: float | None = None
    correlation: float | None = None
    corr_days: int = 0


def holdings_overlap(top_a: list[dict], top_b: list[dict]):
    wa = {h["symbol"]: h["weight"] for h in top_a}
    wb = {h["symbol"]: h["weight"] for h in top_b}
    shared = sorted(((s, wa[s], wb[s]) for s in wa.keys() & wb.keys()), key=lambda x: -min(x[1], x[2]))
    return sum(min(a, b) for _, a, b in shared), sum(wa.values()), sum(wb.values()), shared


def sector_overlap(sa: dict, sb: dict) -> float | None:
    if not sa or not sb:
        return None
    return sum(min(sa.get(k, 0), sb.get(k, 0)) for k in sa.keys() | sb.keys())


def return_correlation(close_a: pd.Series, close_b: pd.Series, days: int = 252):
    r = pd.concat([close_a.pct_change(), close_b.pct_change()], axis=1, join="inner").dropna().tail(days)
    if len(r) < 60:
        return None, len(r)
    return float(r.iloc[:, 0].corr(r.iloc[:, 1])), len(r)


def compare(profile_a: dict, profile_b: dict, close_a: pd.Series | None, close_b: pd.Series | None) -> Overlap:
    h, ca, cb, shared = holdings_overlap(profile_a.get("top_holdings", []), profile_b.get("top_holdings", []))
    ov = Overlap(h, ca, cb, shared, sector_overlap(profile_a.get("sectors"), profile_b.get("sectors")))
    if close_a is not None and close_b is not None and not close_a.empty and not close_b.empty:
        ov.correlation, ov.corr_days = return_correlation(close_a, close_b)
    return ov


def similarity_label(ov: Overlap) -> str:
    """Plain-language read of how much two funds move as one."""
    c, s = ov.correlation, ov.sector_pct
    if c is None:
        return "not enough shared price history to judge"
    if c >= 0.95 and (s is None or s >= 80):
        return "nearly the same exposure: holding both adds little diversification"
    if c >= 0.85:
        return "highly similar: they mostly move together"
    if c >= 0.6:
        return "related but meaningfully different"
    return "different exposures: they diversify each other"


def annual_fee(expense_ratio_pct: float | None, amount: float) -> float | None:
    return None if expense_ratio_pct is None else amount * expense_ratio_pct / 100


def fee_drag(expense_ratio_pct: float | None, amount: float, years: int = 10,
             growth_pct: float = 7.0) -> float | None:
    """How much less you'd have after `years` versus a zero-fee fund growing at growth_pct."""
    if expense_ratio_pct is None:
        return None
    g, e = growth_pct / 100, expense_ratio_pct / 100
    return amount * ((1 + g) ** years - (1 + g - e) ** years)


def is_stale(profile: dict | None, today: date | None = None) -> bool:
    if profile is None:
        return True
    today = today or date.today()
    return profile["as_of"] <= (today - timedelta(days=PROFILE_MAX_AGE_DAYS)).isoformat()


def get_profile(db, symbol: str, provider=None, today: date | None = None) -> dict | None:
    """Stored profile if fresh, otherwise fetch and store. Falls back to a stale copy."""
    cached = db.get_etf_profile(symbol)
    if not is_stale(cached, today):
        return cached
    if provider is None:
        from app.providers.yahoo_funds import YahooFunds
        provider = YahooFunds()
    fresh = provider.get_profile(symbol)
    if fresh is None:
        return cached
    as_of = (today or date.today()).isoformat()
    db.upsert_etf_profile(symbol, as_of, provider.name, fresh)
    return {**fresh, "as_of": as_of}

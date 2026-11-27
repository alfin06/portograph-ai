"""Dividends from Yahoo: past ex-dates with amounts, plus the next ex-date when the
company has announced one (IDX companies usually announce a few weeks ahead).

Yahoo gives no payment dates for IDX stocks, and ETFs have no announced dates, so the
calendar estimates those from the same period last year (see app.analytics.dividends)."""
import logging
import math
from datetime import date, datetime, timezone

log = logging.getLogger(__name__)

SOURCE = "yahoo"
HISTORY_YEARS = 3


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) or f <= 0 else f


def fetch_raw(yahoo_symbol: str) -> dict:
    """The only network part: past dividends, the calendar and two info fields."""
    import yfinance as yf
    t = yf.Ticker(yahoo_symbol)
    out = {"history": [], "calendar": {}, "info": {}}
    try:
        s = t.dividends
        out["history"] = [(i.date().isoformat(), float(v)) for i, v in s.items()]
    except Exception as e:
        log.warning("%s: no dividend history (%s)", yahoo_symbol, type(e).__name__)
    try:
        cal = t.calendar
        out["calendar"] = cal if isinstance(cal, dict) else {}
    except Exception:
        pass                      # ETFs have no calendar: Yahoo answers 404
    try:
        info = t.info or {}
        out["info"] = {k: info.get(k) for k in ("lastDividendDate", "lastDividendValue")}
    except Exception:
        pass
    return out


def normalize(raw: dict, today: date) -> list[tuple[str, float | None]]:
    """-> [(ex_date, amount per share or None)], past ~3 years plus an announced
    upcoming one. The amount of an upcoming ex-date is known when Yahoo's 'last
    dividend' already points at that date."""
    cutoff = date(today.year - HISTORY_YEARS, today.month, 1).isoformat()
    rows = {d: _num(a) for d, a in raw.get("history", []) if d >= cutoff}
    ex = (raw.get("calendar") or {}).get("Ex-Dividend Date")
    if isinstance(ex, datetime):
        ex = ex.date()
    if isinstance(ex, date) and ex >= today:
        info = raw.get("info") or {}
        amount = None
        ts = info.get("lastDividendDate")
        if ts:
            try:
                if datetime.fromtimestamp(int(ts), tz=timezone.utc).date() == ex:
                    amount = _num(info.get("lastDividendValue"))
            except (TypeError, ValueError, OverflowError, OSError):
                pass
        rows[ex.isoformat()] = rows.get(ex.isoformat()) or amount
    return sorted(rows.items())


class YahooDividends:
    def get(self, yahoo_symbol: str, today: date) -> list[tuple[str, float | None]]:
        return normalize(fetch_raw(yahoo_symbol), today)

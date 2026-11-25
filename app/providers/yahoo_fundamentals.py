"""Fundamental ratios from Yahoo's quote summary (yfinance `Ticker.info`).

Yahoo mixes units, so everything is normalized here:
  - ROE, ROA, margins, growth: fraction -> percent (0.21 -> 21.0)
  - dividendYield: already percent in current yfinance (6.57 = 6.57%)
  - debtToEquity: percent -> ratio (84.3 -> 0.843)
  - P/B: recomputed when the company reports in a different currency than it trades in
    (e.g. ITMG reports in USD, trades in IDR; Yahoo's own P/B is then ~13,000x)
"""
import logging
import math

from app.symbols import Instrument

log = logging.getLogger(__name__)

SOURCE = "yahoo"


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _pct(v) -> float | None:
    f = _num(v)
    return None if f is None else f * 100


def to_usd(amount: float | None, currency: str, usdidr: float | None) -> float | None:
    if amount is None:
        return None
    if currency == "USD":
        return amount
    if currency == "IDR" and usdidr:
        return amount / usdidr
    return None


def _convert(amount: float, from_ccy: str, to_ccy: str, usdidr: float | None) -> float | None:
    if from_ccy == to_ccy:
        return amount
    if usdidr and (from_ccy, to_ccy) == ("USD", "IDR"):
        return amount * usdidr
    if usdidr and (from_ccy, to_ccy) == ("IDR", "USD"):
        return amount / usdidr
    return None


def normalize_info(info: dict, trade_ccy: str, usdidr: float | None) -> dict:
    """Map a raw Yahoo info dict onto our fundamentals columns."""
    fin_ccy = (info.get("financialCurrency") or trade_ccy).upper()
    price = _num(info.get("currentPrice")) or _num(info.get("regularMarketPrice"))
    book = _num(info.get("bookValue"))

    pb = _num(info.get("priceToBook"))
    if fin_ccy != trade_ccy:
        book_in_trade = _convert(book, fin_ccy, trade_ccy, usdidr) if book else None
        pb = price / book_in_trade if price and book_in_trade else None

    der = _num(info.get("debtToEquity"))
    market_cap = _num(info.get("marketCap"))
    return {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": market_cap,
        "market_cap_usd": to_usd(market_cap, trade_ccy, usdidr),
        "pe": _num(info.get("trailingPE")),
        "forward_pe": _num(info.get("forwardPE")),
        "pb": pb,
        "roe": _pct(info.get("returnOnEquity")),
        "roa": _pct(info.get("returnOnAssets")),
        "der": None if der is None else der / 100,
        "div_yield": _num(info.get("dividendYield")),
        "net_margin": _pct(info.get("profitMargins")),
        "rev_growth": _pct(info.get("revenueGrowth")),
        "eps_growth": _pct(info.get("earningsGrowth")),
    }


class YahooFundamentals:
    name = SOURCE

    def __init__(self, info_fetcher=None):
        # info_fetcher(yahoo_symbol) -> dict; injectable so tests run offline
        if info_fetcher is None:
            import yfinance as yf
            info_fetcher = lambda s: yf.Ticker(s).info  # noqa: E731
        self._info = info_fetcher

    def get_fundamentals(self, instruments: list[Instrument], usdidr: float | None) -> dict[str, dict]:
        out = {}
        for inst in instruments:
            try:
                info = self._info(inst.yahoo) or {}
            except Exception as e:  # one bad symbol must not stop the rest
                log.warning("Fundamentals failed for %s: %s", inst.symbol, e)
                continue
            if not info or info.get("quoteType") is None:
                log.warning("No fundamentals for %s", inst.symbol)
                continue
            out[inst.symbol] = normalize_info(info, inst.currency, usdidr)
        return out

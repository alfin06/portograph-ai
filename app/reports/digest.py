"""Turns snapshots into a Telegram message (HTML parse mode)."""
from html import escape

import pandas as pd

from app.analytics.snapshot import Snapshot

MARKET_TITLE = {"IDX": "🇮🇩 IDX close", "US": "🇺🇸 US close"}


def _price(v: float, currency: str) -> str:
    return f"{v:,.0f}" if currency == "IDR" else f"{v:,.2f}"


def _pct(v: float | None) -> str:
    return "  n/a" if v is None else f"{v:+.1f}%"


def format_digest(market: str, snaps: list[Snapshot], session_date: pd.Timestamp,
                  fx: tuple[float | None, float | None] = (None, None)) -> str:
    title = f"<b>{MARKET_TITLE.get(market, market)} · {session_date:%a %d %b %Y}</b>"
    if not snaps:
        return f"{title}\nNo price data for this market. Check the watchlist or the data feed."

    # Monospace table; kept narrow so it fits a phone screen without wrapping
    rows = [f"{'':1}{'SYM':<6}{'Close':>10}{'1D':>7}{'1M':>7}{'RSI':>4}"]
    for s in sorted(snaps, key=lambda s: (not s.is_holding, s.symbol)):
        rsi = "-" if s.rsi is None else f"{s.rsi:.0f}"
        stale = "" if s.date == session_date else "†"
        rows.append(
            f"{'•' if s.is_holding else ' '}{s.symbol + stale:<6}{_price(s.close, s.currency):>10}"
            f"{_pct(s.chg_1d):>7}{_pct(s.chg_1m):>7}{rsi:>4}"
        )
    parts = [title, "<pre>" + escape("\n".join(rows)) + "</pre>"]

    movers = [s for s in snaps if s.chg_1d is not None and s.date == session_date]
    if len(movers) >= 2:
        best = max(movers, key=lambda s: s.chg_1d)
        worst = min(movers, key=lambda s: s.chg_1d)
        parts.append(f"Best: <b>{escape(best.symbol)}</b> {_pct(best.chg_1d)} · "
                     f"Worst: <b>{escape(worst.symbol)}</b> {_pct(worst.chg_1d)}")

    signals = [(s.symbol, sig) for s in snaps for sig in s.signals]
    if signals:
        parts.append("<b>Signals</b>\n" + "\n".join(
            f"• <b>{escape(sym)}</b> {escape(sig)}" for sym, sig in signals))
    else:
        parts.append("No signals today.")

    rate, chg = fx
    if rate is not None:
        parts.append(f"USD/IDR {rate:,.0f} ({_pct(chg).strip()})")

    legend = "• = holding"
    if any(s.date != session_date for s in snaps):
        legend += " · † = no bar for this session"
    parts.append(f"<i>{legend}. Research only, not investment advice.</i>")
    return "\n\n".join(parts)

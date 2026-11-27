"""Turns snapshots into a Telegram message (HTML parse mode)."""
from html import escape

import pandas as pd

from app.analytics.snapshot import Snapshot

MARKET_TITLE = {"IDX": "🇮🇩 IDX close", "US": "🇺🇸 US close"}


def _price(v: float, currency: str) -> str:
    return f"{v:,.0f}" if currency == "IDR" else f"{v:,.2f}"


def _pct(v: float | None) -> str:
    return "  n/a" if v is None else f"{v:+.1f}%"


def format_screens(screens) -> str:
    lines = ["<b>Screens</b>"]
    for r in screens:
        if r.error:
            body = "⚠️ rule error, fix with /addscreen"
        elif not r.hits:
            body = "no matches"
        else:
            body = ", ".join(f"<b>{escape(s)}</b> 🆕" if s in r.new else escape(s) for s in r.hits)
        lines.append(f"• {escape(r.name)}: {body}")
    return "\n".join(lines)


MOOD_EMOJI = {"Extreme fear": "😱", "Fear": "😟", "Neutral": "😐", "Greed": "🙂", "Extreme greed": "🤑"}


def format_mood(mood, with_drivers: bool = True) -> str:
    line = f"{MOOD_EMOJI[mood.label]} Mood <b>{mood.score:.0f}</b> · {mood.label}"
    if mood.week_ago is not None:
        line += f" (1w ago {mood.week_ago:.0f})"
    drivers = mood.drivers() if with_drivers else []
    if drivers:
        line += " · " + escape(", ".join(drivers))
    return line


def format_zones(events) -> str:
    lines = ["<b>🎯 Buy zones</b>"]
    for e in events:
        z = e.zone
        rng = f"{_price(z['low'], z['currency'])}–{_price(z['high'], z['currency'])}"
        if e.state == "inside":
            what = f"in zone {rng}"
        else:
            what = f"below zone {rng}: fell through, recheck the reason"
        new = " 🆕" if e.new else ""
        note = f" · {escape(z['note'])}" if z.get("note") else ""
        lines.append(f"• <b>{escape(z['symbol'])}</b> {_price(e.close, z['currency'])} {what}{new}{note}")
    return "\n".join(lines)


def _idr(v: float) -> str:
    """Same style as /portfolio: 'Rp 1,002.9 juta', 'Rp 350 rb'."""
    a = abs(v)
    return f"Rp {a / 1e6:,.1f} juta" if a >= 1e6 else f"Rp {a / 1e3:,.0f} rb"


def _signed(v: float) -> str:
    return ("+" if v >= 0 else "−") + _idr(v)


def format_portfolio(market: str, sec) -> str:
    lines = [f"<b>💼 Portfolio</b> {_idr(sec.value_idr)}"]
    if sec.market_change_idr is not None:
        pct = f" ({sec.market_change_pct:+.1f}%)" if sec.market_change_pct is not None else ""
        lines.append(f"{market} holdings today {_signed(sec.market_change_idr)}{pct}")
    if sec.week_change_idr is not None:
        pct = f" ({sec.week_change_pct:+.1f}%)" if sec.week_change_pct is not None else ""
        lines.append(f"Past week {_signed(sec.week_change_idr)}{pct}")
    unreal = f" ({sec.unrealised_pct:+.1f}%)" if sec.unrealised_pct is not None else ""
    lines.append(f"Unrealised {_signed(sec.unrealised_idr)}{unreal}")
    for sym, pct, idr in sec.big_moves:
        lines.append(f"{'📈' if pct > 0 else '📉'} <b>{escape(sym)}</b> {pct:+.1f}% ({_signed(idr)})")
    for text in sec.new_flags:
        lines.append(f"⚠️ {escape(text)}")
    for name in sec.cleared:
        lines.append(f"✅ {escape(name)} is back within the concentration limit")
    if sec.still_flagged:
        lines.append(f"<i>{sec.still_flagged} concentration warning{'s' if sec.still_flagged > 1 else ''} "
                     f"still open, see /portfolio</i>")
    return "\n".join(lines)


def format_coming_up(c) -> str:
    from app.commands import div_line, record_hint   # shared with /dividends

    parts = []
    if c.went_ex or c.dividends:
        lines = ["<b>💰 Dividends</b>"]
        for e in c.went_ex:
            lines.append(div_line(e, with_cum=False).replace(" ex ", " went ex ", 1)
                         + f"\n  when the cash arrives: <code>{escape(record_hint(e))}</code>")
        lines += [div_line(e) for e in c.dividends]
        parts.append("\n".join(lines))
    if c.events:
        parts.append("<b>📅 Coming up</b>\n" + "\n".join(
            f"{e.icon} {e.day:%a %d %b} {escape(e.title)}" for e in c.events))
    if c.calendar_stale:
        parts.append("<i>⚠️ The macro calendar (app/reference/calendar.json) needs next year's dates.</i>")
    return "\n\n".join(parts)


def format_digest(market: str, snaps: list[Snapshot], session_date: pd.Timestamp,
                  fx: tuple[float | None, float | None] = (None, None), screens=None,
                  mood=None, zones=None, portfolio=None, coming=None) -> str:
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
    parts = [title]
    if mood is not None:
        parts.append(format_mood(mood))
    parts.append("<pre>" + escape("\n".join(rows)) + "</pre>")
    if portfolio is not None:
        parts.append(format_portfolio(market, portfolio))

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

    if zones:
        parts.append(format_zones(zones))

    if coming is not None:
        text = format_coming_up(coming)
        if text:
            parts.append(text)

    if screens:
        parts.append(format_screens(screens))

    rate, chg = fx
    if rate is not None:
        parts.append(f"USD/IDR {rate:,.0f} ({_pct(chg).strip()})")

    legend = "• = holding"
    if any(s.date != session_date for s in snaps):
        legend += " · † = no bar for this session"
    parts.append(f"<i>{legend}. Research only, not investment advice.</i>")
    return "\n\n".join(parts)

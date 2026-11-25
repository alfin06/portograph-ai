"""Logic behind the Telegram commands. Each function takes the DB and the command's
arguments and returns an HTML message, so it can be tested without Telegram.

  /watch SYM idx|us [etf] [hold]   add to the watchlist (checks Yahoo has data first)
  /unwatch SYM                     stop tracking
  /fund SYM                        fundamentals + price snapshot
  /screens                         saved screens and the fields rules can use
  /screen NAME | RULE              run a saved screen or an ad-hoc rule now
  /addscreen NAME [idx|us] RULE    save (or replace) a screen
  /delscreen NAME                  delete a screen
"""
import ast
import re
from html import escape

from app.analytics.screener import FIELDS, RuleError, collect_metrics, parse_rule
from app.analytics.snapshot import build_snapshot, market_snapshots
from app.jobs import update_fundamentals
from app.jobs.update_prices import last_closed_session, top_up
from app.symbols import Instrument

MARKETS = {"idx": "IDX", "us": "US"}
CURRENCY = {"IDX": "IDR", "US": "USD"}
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")


def _fmt(v, suffix="", digits=1, signed=False):
    if v is None:
        return "n/a"
    return f"{v:+,.{digits}f}{suffix}" if signed else f"{v:,.{digits}f}{suffix}"


def _price(v, currency):
    return "n/a" if v is None else (f"{v:,.0f}" if currency == "IDR" else f"{v:,.2f}")


# --- watchlist ---------------------------------------------------------------
def cmd_watch(db, args: list[str], provider=None, fund_provider=None) -> str:
    usage = "Usage: /watch SYMBOL idx|us [etf] [hold]\nExample: /watch BBNI idx hold"
    if len(args) < 2 or args[1].lower() not in MARKETS:
        return usage
    symbol, market = args[0].upper(), MARKETS[args[1].lower()]
    flags = {a.lower() for a in args[2:]}
    if flags - {"etf", "hold"}:
        return usage
    inst = Instrument(symbol, market, "ETF" if "etf" in flags else "STOCK", CURRENCY[market])

    if provider is None:
        from app.providers.yahoo import YahooProvider
        provider = YahooProvider()
    bars = provider.get_history([inst], period="2y").get(symbol)
    if bars is None or bars.empty:
        return f"❌ No price data for {escape(inst.yahoo)} on Yahoo. Check the symbol and market."

    db.add_instrument(symbol, market, inst.asset_type, inst.currency, is_holding="hold" in flags)
    db.upsert_price_bars(symbol, bars)
    try:
        update_fundamentals.run(db=db, provider=fund_provider, market=market)
    except Exception:
        pass  # the next digest retries; prices are what matters for /watch
    role = "holding" if "hold" in flags else "watchlist"
    return f"✅ Added <b>{escape(symbol)}</b> ({market} {inst.asset_type.lower()}, {role}), {len(bars)} days of prices."


def cmd_unwatch(db, args: list[str]) -> str:
    if len(args) != 1:
        return "Usage: /unwatch SYMBOL"
    sym = args[0].upper()
    return f"🗑 Stopped tracking <b>{escape(sym)}</b>." if db.set_instrument_active(sym, False) \
        else f"{escape(sym)} isn't on your watchlist."


# --- fundamentals --------------------------------------------------------------
def cmd_fund(db, args: list[str], fund_provider=None) -> str:
    if len(args) != 1:
        return "Usage: /fund SYMBOL   e.g. /fund BBCA"
    inst = db.get_instrument(args[0])
    if inst is None or not inst["active"]:
        return f"{escape(args[0].upper())} isn't on your watchlist. Add it first: /watch {escape(args[0].upper())} idx|us"

    f = db.get_latest_fundamentals(inst["symbol"])
    if f is None:
        try:
            update_fundamentals.run(db=db, provider=fund_provider, market=inst["market"])
        except Exception:
            pass
        f = db.get_latest_fundamentals(inst["symbol"])
    snap = build_snapshot(inst, db.get_price_history(inst["symbol"], days=300))
    ccy = inst["currency"]

    lines = [f"<b>{escape(inst['symbol'])}</b> · {inst['market']} {inst['asset_type'].lower()}"
             + (" · holding" if inst["is_holding"] else "")]
    if f and (f.get("sector") or f.get("industry")):
        lines.append(escape(" / ".join(x for x in (f.get("sector"), f.get("industry")) if x)))
    if snap:
        lines.append(f"Close {_price(snap.close, ccy)} {ccy} ({_fmt(snap.chg_1d, '%', signed=True)} 1D, "
                     f"{_fmt(snap.chg_1m, '%', signed=True)} 1M) · RSI {_fmt(snap.rsi, digits=0)}")
    if not f:
        lines.append("No fundamentals available from Yahoo yet.")
        return "\n".join(lines)
    cap = f.get("market_cap_usd")
    lines += [
        "",
        f"P/E {_fmt(f['pe'])} · Fwd P/E {_fmt(f['forward_pe'])} · P/B {_fmt(f['pb'], digits=2)}",
        f"ROE {_fmt(f['roe'], '%')} · ROA {_fmt(f['roa'], '%')} · D/E {_fmt(f['der'], 'x', digits=2)}",
        f"Div yield {_fmt(f['div_yield'], '%')} · Net margin {_fmt(f['net_margin'], '%')}",
        f"Revenue growth {_fmt(f['rev_growth'], '%', signed=True)} · EPS growth {_fmt(f['eps_growth'], '%', signed=True)}",
        f"Market cap {'n/a' if cap is None else f'${cap / 1e9:,.1f}B'}",
        "",
        f"<i>Fundamentals: Yahoo, as of {f['as_of']}. Growth is year-on-year for the latest quarter.</i>",
    ]
    return "\n".join(lines)


# --- screens -------------------------------------------------------------------
def cmd_screens(db) -> str:
    screens = db.get_screens(active_only=False)
    lines = ["<b>Saved screens</b>"]
    for s in screens:
        scope = s["market"] or "all"
        lines.append(f"• <b>{escape(s['name'])}</b> [{scope}]: <code>{escape(s['rule'])}</code>")
    if not screens:
        lines.append("None yet.")
    lines += ["", "<b>Fields</b>"] + [f"<code>{k}</code> {escape(v)}" for k, v in FIELDS.items()]
    lines += ["", "Run: /screen NAME or /screen roe &gt; 15 and pb &lt; 2",
              "Save: /addscreen NAME [idx|us] RULE · Delete: /delscreen NAME"]
    return "\n".join(lines)


def _rule_fields(rule) -> list[str]:
    seen = []
    for node in ast.walk(rule.tree):
        if isinstance(node, ast.Name) and node.id not in seen:
            seen.append(node.id)
    return seen


def cmd_screen(db, args: list[str]) -> str:
    if not args:
        return "Usage: /screen NAME  or  /screen RULE (e.g. /screen roe > 15 and pb < 2)"
    saved = db.get_screen(args[0]) if len(args) == 1 else None
    rule_text, market, title = (saved["rule"], saved["market"], saved["name"]) if saved \
        else (" ".join(args), None, "ad-hoc")
    try:
        rule = parse_rule(rule_text)
    except RuleError as e:
        if len(args) == 1:
            return f"No saved screen called '{escape(args[0])}'. See /screens."
        return f"❌ {escape(str(e))}"

    snaps = []
    for m in ([market] if market else ["IDX", "US"]):
        snaps += market_snapshots(db, m)
    metrics = collect_metrics(db, snaps)
    hits = sorted(sym for sym, m in metrics.items() if rule.matches(m))

    head = f"<b>Screen {escape(title)}</b>: <code>{escape(rule.text)}</code>\n" \
           f"{len(hits)} of {len(metrics)} watched symbols match."
    if not hits:
        return head
    fields = [f for f in _rule_fields(rule) if f != "holding"][:4]
    rows = [f"{'SYM':<6}" + "".join(f"{f[:8]:>9}" for f in fields)]
    for sym in hits:
        rows.append(f"{sym:<6}" + "".join(
            f"{'n/a' if metrics[sym][f] is None else f'{metrics[sym][f]:,.1f}':>9}" for f in fields))
    return head + "\n<pre>" + escape("\n".join(rows)) + "</pre>"


def cmd_addscreen(db, args: list[str]) -> str:
    usage = "Usage: /addscreen NAME [idx|us] RULE\nExample: /addscreen cheap_banks idx pb < 1.5 and roe > 12"
    if len(args) < 2:
        return usage
    name = args[0].lower()
    if not NAME_RE.match(name):
        return "Screen names use letters, digits and _ (e.g. cheap_banks)."
    rest = args[1:]
    market = MARKETS.get(rest[0].lower()) if rest else None
    if market:
        rest = rest[1:]
    try:
        rule = parse_rule(" ".join(rest))
    except RuleError as e:
        return f"❌ {escape(str(e))}"
    db.save_screen(name, rule.text, market)
    return (f"✅ Saved <b>{escape(name)}</b> [{market or 'all'}]: <code>{escape(rule.text)}</code>\n"
            f"Its matches will appear in the {'next ' + market if market else 'daily'} digest.")


def cmd_delscreen(db, args: list[str]) -> str:
    if len(args) != 1:
        return "Usage: /delscreen NAME"
    return f"🗑 Deleted screen <b>{escape(args[0].lower())}</b>." if db.delete_screen(args[0]) \
        else f"No screen called '{escape(args[0])}'."



# --- sentiment -------------------------------------------------------------------
def cmd_sentiment(db, args: list[str], provider=None) -> str:
    """/sentiment [idx|us]: fear & greed score with every input explained."""
    from app.analytics import sentiment
    from app.reports.digest import format_mood

    if args and args[0].lower() not in MARKETS:
        return "Usage: /sentiment  or  /sentiment idx|us"
    markets = [MARKETS[args[0].lower()]] if args else ["US", "IDX"]
    if provider is None:
        from app.providers.yahoo import YahooProvider
        provider = YahooProvider()

    blocks = []
    for m in markets:
        try:
            sentiment.refresh(db, provider, m)
        except Exception:
            pass  # fall back to stored data
        mood = sentiment.from_db(db, m)
        flag = "🇺🇸 US" if m == "US" else "🇮🇩 IDX"
        if mood is None:
            blocks.append(f"<b>{flag}</b>\nNot enough data yet.")
            continue
        lines = [f"<b>{flag}</b> · {mood.date:%a %d %b}", format_mood(mood, with_drivers=False)]
        for c in mood.components:
            lines.append(f"• {escape(c.name)}: <b>{c.value:.0f}</b> {escape(c.reading)}")
        blocks.append("\n".join(lines))
    blocks.append("<i>0 = extreme fear, 100 = extreme greed. Each input is ranked against its "
                  "own past year. Our own estimate from Yahoo data, not CNN's index.</i>")
    return "\n\n".join(blocks)


# --- ETFs ------------------------------------------------------------------------
JUTA_100 = 100_000_000  # Rp 100 juta, the reference amount for fee examples


def _pretty_sector(key: str) -> str:
    return key.replace("_", " ").title().replace("Realestate", "Real Estate")


def _us_close(db, symbol: str, provider=None):
    """Price history for a US symbol, fetched and stored if we don't have a year of it."""
    inst = Instrument(symbol.upper(), "US", "ETF", "USD")
    hist = db.get_price_history(symbol)
    if len(hist) < 260:
        if provider is None:
            from app.providers.yahoo import YahooProvider
            provider = YahooProvider()
        got = provider.get_history([inst], period="2y")
        if symbol.upper() in got:
            df = got[symbol.upper()]
            db.upsert_price_bars(symbol.upper(), df[df.index.date <= last_closed_session("US")])
    else:
        top_up(db, [inst], provider)
    hist = db.get_price_history(symbol)
    return None if hist.empty else hist["Close"]


def _idr_short(v: float | None) -> str:
    if v is None:
        return "n/a"
    if v >= 1e6:
        return f"Rp {v / 1e6:,.1f} juta"
    return f"Rp {v / 1e3:,.0f} rb" if v >= 1e3 else f"Rp {v:,.0f}"


def cmd_etf(db, args: list[str], funds_provider=None) -> str:
    from app.analytics import etf

    if len(args) != 1:
        return "Usage: /etf SYMBOL   e.g. /etf VOO (US-listed ETFs)"
    sym = args[0].upper()
    p = etf.get_profile(db, sym, provider=funds_provider)
    if p is None:
        return f"❌ No ETF data for {escape(sym)}. Is it a US-listed ETF?"

    er = p.get("expense_ratio")
    lines = [f"<b>{escape(sym)}</b> · {escape(p.get('name') or '')}",
             " · ".join(escape(x) for x in (p.get("category"), p.get("family"),
                                             p.get("inception") and f"since {p['inception']}") if x),
             f"Expense {_fmt(er, '%', digits=2)}/yr: {_idr_short(etf.annual_fee(er, JUTA_100))} a year per "
             f"Rp 100 juta, ~{_idr_short(etf.fee_drag(er, JUTA_100))} less after 10 years*"]
    aum = p.get("aum_usd")
    lines.append(f"AUM {'n/a' if aum is None else f'${aum / 1e9:,.1f}B'} · Yield {_fmt(p.get('yield'), '%', 2)}"
                 f" · Turnover {_fmt(p.get('turnover'), '%', 0)}")
    top = p.get("top_holdings") or []
    if top:
        lines.append(f"\nTop {len(top)} holdings = {sum(h['weight'] for h in top):.1f}% of the fund")
        rows = [f"{h['symbol'][:6]:<7}{h['name'][:18]:<19}{h['weight']:5.1f}%" for h in top]
        lines.append("<pre>" + escape("\n".join(rows)) + "</pre>")
    sectors = sorted((p.get("sectors") or {}).items(), key=lambda kv: -kv[1])[:5]
    if sectors:
        lines.append("Sectors: " + ", ".join(f"{_pretty_sector(k)} {v:.0f}%" for k, v in sectors))
    lines.append(f"\n<i>*vs a zero-fee fund at 7%/yr. Yahoo data as of {p['as_of']}.</i>")
    return "\n".join(lines)


def cmd_overlap(db, args: list[str], funds_provider=None, provider=None) -> str:
    from app.analytics import etf

    if len(args) != 2:
        return "Usage: /overlap ETF1 ETF2   e.g. /overlap VOO QQQM"
    a, b = (x.upper() for x in args)
    pa, pb = (etf.get_profile(db, s, provider=funds_provider) for s in (a, b))
    missing = [s for s, p in ((a, pa), (b, pb)) if p is None]
    if missing:
        return f"❌ No ETF data for {escape(', '.join(missing))}."
    ov = etf.compare(pa, pb, _us_close(db, a, provider), _us_close(db, b, provider))

    lines = [f"<b>{escape(a)} vs {escape(b)}</b>",
             f"Expense: {escape(a)} {_fmt(pa.get('expense_ratio'), '%', 2)} · "
             f"{escape(b)} {_fmt(pb.get('expense_ratio'), '%', 2)}"]
    if ov.correlation is not None:
        lines.append(f"Return correlation (last {ov.corr_days} days): <b>{ov.correlation:.2f}</b>")
    if ov.sector_pct is not None:
        lines.append(f"Sector overlap: <b>{ov.sector_pct:.0f}%</b>")
    lines.append(f"Top-holdings overlap: <b>{ov.holdings_pct:.1f}%</b> (the top holdings listed are "
                 f"{ov.coverage_a:.0f}% of {escape(a)} and {ov.coverage_b:.0f}% of {escape(b)})")
    if ov.shared:
        rows = [f"{'Shared':<7}{a[:6]:>7}{b[:6]:>7}"] + \
               [f"{s[:6]:<7}{wa:6.1f}%{wb:6.1f}%" for s, wa, wb in ov.shared]
        lines.append("<pre>" + escape("\n".join(rows)) + "</pre>")
    lines.append(f"Read: {escape(etf.similarity_label(ov))}.")
    lines.append("<i>Yahoo only lists each fund's top 10 holdings, so the true holdings overlap is at "
                 "least the figure shown. Correlation and sectors cover the whole fund.</i>")
    return "\n".join(lines)


# --- currency-adjusted returns -------------------------------------------------------
def cmd_compare(db, args: list[str], provider=None, now=None) -> str:
    from app.analytics.fx_returns import PERIOD_NAMES, currency_return

    usage = (f"Usage: /compare SYM SYM ... [{'|'.join(PERIOD_NAMES)}]\n"
             "Example: /compare BBCA VOO QQQM 1y")
    if not args:
        return usage
    period = "1y"
    if args[-1].lower() in PERIOD_NAMES:
        period, args = args[-1].lower(), args[:-1]
    if not args or len(args) > 8:
        return usage

    found, problems, insts = [], [], []
    for raw in args:
        inst = db.get_instrument(raw)
        if inst is None or not inst["active"]:
            problems.append(f"{raw.upper()} isn't on your watchlist (/watch it first)")
            continue
        insts.append(inst)
    try:  # bring stored prices up to the last finished session before comparing
        top_up(db, [Instrument(i["symbol"], i["market"], i["asset_type"], i["currency"]) for i in insts],
               provider, now)
    except Exception as e:
        problems.append(f"couldn't refresh prices ({type(e).__name__}), using stored data")
    usdidr = db.get_fx_history("USDIDR")
    for inst in insts:
        hist = db.get_price_history(inst["symbol"])
        if hist.empty:
            problems.append(f"{inst['symbol']}: no prices stored yet")
            continue
        found.append((inst, hist["Close"]))

    # One common end date (IDX and US close on different days), so every row
    # covers the same window and the same currency move
    results = []
    common_end = min((c.index[-1] for _, c in found), default=None)
    for inst, close in found:
        r = currency_return(inst["symbol"], inst["currency"], close[close.index <= common_end],
                            usdidr, period)
        if r is None:
            problems.append(f"{inst['symbol']}: not enough history for {period}")
        else:
            results.append(r)

    lines = []
    if results:
        end = max(r.end for r in results)
        span = "year to date" if period == "ytd" else f"over {period}"
        lines.append(f"<b>Returns {span}</b> to {end:%d %b %Y}, dividends reinvested")
        rows = [f"{'SYM':<7}{'Local':>7}{'in IDR':>8}{'in USD':>8}"] + [
            f"{r.symbol[:6]:<7}{r.local:+7.1f}{r.idr:+8.1f}{r.usd:+8.1f}" for r in results]
        lines.append("<pre>" + escape("\n".join(rows)) + "</pre>")
        fx = results[0].fx
        weaker = "weakened" if fx > 0 else "strengthened"
        lines.append(f"USD/IDR {fx:+.1f}% (rupiah {weaker}). A weaker rupiah adds to US returns in IDR "
                     f"and cuts IDX returns in USD.")
        us = [r for r in results if r.currency == "USD"]
        for r in us[:3]:
            lines.append(f"• {escape(r.symbol)} in IDR {r.idr:+.1f}% = {r.local:+.1f}% price "
                         f"{'+' if r.fx >= 0 else '−'} {abs(r.fx):.1f}% currency (compounded)")
    if problems:
        lines.append("\n".join(f"⚠️ {escape(p, quote=False)}" for p in problems))
    return "\n".join(lines) if lines else usage


# --- status ----------------------------------------------------------------------------
def cmd_status(db, args: list[str] | None = None, now=None) -> str:
    """Is the always-on part alive? Last digest per market, newest prices, last backup."""
    from datetime import datetime, timezone
    from pathlib import Path

    from core import config

    now = now or datetime.now(timezone.utc)
    lines = ["<b>PortoGraph status</b>"]
    runs = {r["job"]: r for r in db.get_last_job_runs()}
    for market in ("IDX", "US"):
        r = runs.get(f"digest_{market}")
        if r is None:
            lines.append(f"{market} digest: no scheduled run recorded yet")
            continue
        ago = (now - datetime.fromisoformat(r["ran_at"])).total_seconds() / 3600
        lines.append(f"{market} digest: {escape(r['status'])} for {r['session_date']} ({ago:,.0f}h ago)")
    for market in ("IDX", "US"):
        latest = [db.get_price_history(i["symbol"], days=10) for i in db.get_instruments(market=market)]
        dates = [h.index[-1] for h in latest if not h.empty]
        lines.append(f"{market} prices to: {max(dates):%a %d %b}" if dates else f"{market} prices: none")
    backups = sorted(Path(config.BACKUP_DIR).glob("portograph-*.db"))
    if backups:
        b = backups[-1]
        lines.append(f"Last backup: {b.name} ({b.stat().st_size / 1024:,.0f} KB, {len(backups)} kept)")
    else:
        lines.append("Last backup: none yet (the worker makes one daily at 03:15)")
    return "\n".join(lines)

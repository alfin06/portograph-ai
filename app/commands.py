"""Logic behind the Telegram commands. Each function takes the DB and the command's
arguments and returns an HTML message, so it can be tested without Telegram.

  /watch SYM idx|us [etf] [hold]   add to the watchlist (checks Yahoo has data first)
  /unwatch SYM                     stop tracking
  /fund SYM                        fundamentals + price snapshot
  /screens                         saved screens and the fields rules can use
  /screen NAME | RULE              run a saved screen or an ad-hoc rule now
  /addscreen NAME [idx|us] RULE    save (or replace) a screen
  /delscreen NAME                  delete a screen
  /buy, /sell SYM QTY PRICE [DATE] record a trade (IDX quantity in lots)
  /div SYM AMOUNT [DATE]           record a dividend
  /txns [SYM], /deltxn ID          list or delete transactions
  /portfolio                       holdings, profit in IDR, allocation
  /price SYM                       latest close, nearest zone, your position
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
    from app.analytics.fx_returns import PERIOD_NAMES, compare_closes

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
        found.append((inst["symbol"], inst["currency"], hist["Close"]))

    cmp = compare_closes(found, usdidr, period)
    results = cmp.results
    problems += cmp.problems

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


# --- valuation bands -----------------------------------------------------------------
def _watched(db, raw: str):
    inst = db.get_instrument(raw)
    if inst is None or not inst["active"]:
        return None, (f"{escape(raw.upper())} isn't on your watchlist. "
                      f"Add it first: /watch {escape(raw.upper())} idx|us")
    return Instrument(inst["symbol"], inst["market"], inst["asset_type"], inst["currency"]), None


def _latest_close(db, inst, provider=None, now=None):
    try:
        top_up(db, [inst], provider, now)
    except Exception:
        pass  # stored prices are still usable
    hist = db.get_price_history(inst.symbol, days=10)
    return (None, None) if hist.empty else (float(hist["Close"].iloc[-1]), hist.index[-1])


def cmd_value(db, args: list[str], provider=None, val_provider=None, now=None) -> str:
    from app.analytics import valuation as val

    if len(args) != 1:
        return "Usage: /value SYMBOL   e.g. /value BBCA (stocks on your watchlist)"
    inst, err = _watched(db, args[0])
    if err:
        return err
    if inst.asset_type == "ETF":
        return f"{escape(inst.symbol)} is an ETF: valuation bands are for single stocks. Try /etf {escape(inst.symbol)}"
    close, when = _latest_close(db, inst, provider, now)
    if close is None:
        return f"{escape(inst.symbol)}: no prices stored yet."
    profile = val.get_profile(db, inst, provider=val_provider)
    if profile is None:
        return f"❌ Not enough history on Yahoo to build valuation bands for {escape(inst.symbol)}."
    readings = val.assess(profile, close)
    if not readings:
        return f"{escape(inst.symbol)}: no usable P/E, P/B or dividend history (losses and no dividends?)."

    ccy = inst.currency
    lines = [f"<b>{escape(inst.symbol)}</b> · valuation vs its own last {profile['years']:.1f} years",
             f"Close {_price(close, ccy)} {ccy} ({when:%d %b})"]
    rows = [f"{'':6}{'Now':>7}{'Rank':>6}  {'Low–Typical–High'}"]
    for r in readings:
        unit = "%" if r.metric == "yield" else "x"
        d = 2 if r.metric == "pb" else 1
        rows.append(f"{val.LABEL[r.metric]:<6}{r.value:>6.{d}f}{unit}{r.rank:>5.0f}%  "
                    f"{r.low:.{d}f}–{r.typical:.{d}f}–{r.high:.{d}f}{unit}")
    lines.append("<pre>" + escape("\n".join(rows)) + "</pre>")
    lines.append("<i>Rank: % of past trading days with a lower value. Low/High = 10th/90th percentile.</i>")
    for r in readings:
        lines.append(f"• {val.LABEL[r.metric]}: {r.label}")

    lines.append("\n<b>Prices that match its usual levels</b>")
    for r in readings:
        cheap_name = "75th pct yield" if r.metric == "yield" else "25th pct"
        lines.append(f"• {val.LABEL[r.metric]}: cheap side below {_price(r.price_cheap, ccy)} ({cheap_name}) · "
                     f"typical {_price(r.price_typical, ccy)}")

    if profile.get("roe"):
        lines.append("\nROE: " + " · ".join(f"{x['period']} {x['roe']:.1f}%" for x in profile["roe"][-5:]))
    warn = val.roe_warning(profile.get("roe", []))
    if warn:
        lines.append(f"⚠️ {escape(warn)}")

    cheap = [r for r in readings if r.cheap_side == "cheap"]
    pricey = [r for r in readings if r.cheap_side == "expensive"]
    lines.append(f"\nRead: {len(cheap)} of {len(readings)} measures on the cheap side, "
                 f"{len(pricey)} on the expensive side.")
    r = readings[0]
    if close > r.price_cheap:
        fmt = ".0f" if ccy == "IDR" else ".2f"
        lines.append(f"Alert me in its cheapest quarter by {val.LABEL[r.metric]}: "
                     f"/zone {escape(inst.symbol)} {r.price_deep:{fmt}} {r.price_cheap:{fmt}}")
    else:
        lines.append(f"It is already below the cheap boundary by {val.LABEL[r.metric]} "
                     f"({_price(r.price_cheap, ccy)}).")
    lines.append("<i>Bands only cover these years and assume the business is comparable. Cyclicals "
                 "(miners) look cheapest on P/E at peak earnings. Research only, not investment advice.</i>")
    return "\n".join(lines)


# --- numbers typed in Telegram ---------------------------------------------------------
_SUFFIX = {"rb": 1e3, "k": 1e3, "jt": 1e6, "juta": 1e6, "m": 1e6, "mio": 1e6,
           "b": 1e9, "bn": 1e9, "miliar": 1e9, "t": 1e12, "triliun": 1e12}
_THOUSANDS = re.compile(r"^\d{1,3}([.,]\d{3})+$")


def parse_number(text: str) -> float | None:
    """'5500', '5,500', '5.500' (IDR style), '5500.5', '150jt', '1.5m', '2b' -> float."""
    s = text.strip().lower().replace("rp", "").replace("_", "")
    m = re.fullmatch(r"([\d.,]+)\s*([a-z]*)", s)
    if not m:
        return None
    num, suf = m.groups()
    if suf and suf not in _SUFFIX:
        return None
    if _THOUSANDS.match(num) and not suf:
        num = re.sub(r"[.,]", "", num)
    else:
        num = num.replace(",", "") if num.count(",") and num.count(".") else num.replace(",", ".")
    try:
        v = float(num)
    except ValueError:
        return None
    return v * _SUFFIX.get(suf, 1)


# --- buy zones -------------------------------------------------------------------------
def cmd_zone(db, args: list[str], provider=None, now=None) -> str:
    from datetime import datetime

    from app.analytics.zones import distance_pct, state_of

    usage = ("Usage: /zone SYMBOL LOW HIGH [note]\nExample: /zone BBCA 5500 5950 P/B cheap quarter\n"
             "You'll get a line in the daily digest when it closes inside (or below) the zone.")
    if len(args) < 3:
        return usage
    inst, err = _watched(db, args[0])
    if err:
        return err
    low, high = parse_number(args[1]), parse_number(args[2])
    if low is None or high is None or low <= 0 or high <= 0:
        return usage
    low, high = min(low, high), max(low, high)
    note = " ".join(args[3:])[:80] or None
    zid = db.add_zone(inst.symbol, low, high, note, datetime.now().strftime("%Y-%m-%d"))
    ccy = inst.currency
    msg = f"✅ Zone #{zid}: {escape(inst.symbol)} {_price(low, ccy)}–{_price(high, ccy)} {ccy}"
    close, _ = _latest_close(db, inst, provider, now)
    if close is not None:
        st = state_of(close, low, high)
        where = {"inside": "is inside it now", "below": "is already below it"}.get(
            st, f"needs to fall {abs(distance_pct(close, low, high)):.1f}% to reach it")
        msg += f"\nLast close {_price(close, ccy)} {where}."
    return msg


def cmd_zones(db, args: list[str] | None = None) -> str:
    from app.analytics.zones import distance_pct, state_of

    zs = db.get_zones()
    if not zs:
        return "No buy zones yet. Add one: /zone BBCA 5500 5950   (see /value BBCA for levels)"
    lines = ["<b>Buy zones</b>"]
    for z in zs:
        hist = db.get_price_history(z["symbol"], days=10)
        ccy = z["currency"]
        where = ""
        if not hist.empty:
            close = float(hist["Close"].iloc[-1])
            st = state_of(close, z["low"], z["high"])
            where = (f" · now {_price(close, ccy)}, "
                     + ({"inside": "inside", "below": "below"}.get(st)
                        or f"{abs(distance_pct(close, z['low'], z['high'])):.1f}% above"))
        note = f" · {escape(z['note'])}" if z.get("note") else ""
        lines.append(f"#{z['id']} <b>{escape(z['symbol'])}</b> {_price(z['low'], ccy)}–{_price(z['high'], ccy)}"
                     f"{where}{note}")
    lines.append("Delete one: /delzone ID")
    return "\n".join(lines)


def cmd_delzone(db, args: list[str]) -> str:
    if len(args) != 1 or not args[0].lstrip("#").isdigit():
        return "Usage: /delzone ID   (IDs are listed by /zones)"
    zid = int(args[0].lstrip("#"))
    return f"🗑 Zone #{zid} deleted." if db.delete_zone(zid) else f"No zone #{zid}. See /zones."


# --- position sizing -------------------------------------------------------------------
def cmd_capital(db, args: list[str]) -> str:
    if not args:
        cap = db.get_setting("capital_idr")
        if cap is None:
            return "No capital set. Set it: /capital 150jt   (your total investable amount, in IDR)"
        return f"Capital for /size: {_idr_short(float(cap))}. Change it: /capital 200jt"
    v = parse_number(args[0])
    if v is None or v <= 0:
        return "Usage: /capital AMOUNT   e.g. /capital 150jt, /capital 1.5m, /capital 150000000"
    db.set_setting("capital_idr", v)
    return f"✅ Capital set to {_idr_short(v)} (Rp {v:,.0f}). /size uses this."


def cmd_size(db, args: list[str], provider=None, now=None) -> str:
    from app.analytics.sizing import DEFAULT_MAX_POSITION_PCT, DEFAULT_RISK_PCT, size_position

    usage = ("Usage: /size SYMBOL ENTRY STOP [RISK%] [MAX%]\n"
             "Example: /size BBCA 5800 5400 1%   (ENTRY can be 'now')\n"
             f"RISK% = capital you accept losing if the stop is hit (default {DEFAULT_RISK_PCT:g}%); "
             f"MAX% = largest position allowed (default {DEFAULT_MAX_POSITION_PCT:g}%).")
    if len(args) < 3:
        return usage
    cap = db.get_setting("capital_idr")
    if cap is None:
        return "Set your capital first: /capital 150jt"
    inst, err = _watched(db, args[0])
    if err:
        return err
    if args[1].lower() == "now":
        entry, _ = _latest_close(db, inst, provider, now)
    else:
        entry = parse_number(args[1])
    stop = parse_number(args[2])
    pcts = [parse_number(a.rstrip("%")) for a in args[3:5]]
    if entry is None or stop is None or any(p is None for p in pcts):
        return usage
    risk = pcts[0] if len(pcts) > 0 else DEFAULT_RISK_PCT
    max_pos = pcts[1] if len(pcts) > 1 else DEFAULT_MAX_POSITION_PCT
    try:
        s = size_position(float(cap), entry, stop, inst.market, inst.currency,
                          usdidr=db.get_latest_fx("USDIDR"), risk_pct=risk, max_position_pct=max_pos)
    except ValueError as e:
        return f"❌ {escape(str(e))}\n\n{usage}"

    ccy, sym = inst.currency, escape(inst.symbol)
    lines = [f"<b>{sym}</b> position size · capital {_idr_short(float(cap))}",
             f"Entry {_price(entry, ccy)} · stop {_price(stop, ccy)} ({s.stop_distance_pct:.1f}% below) · "
             f"risk {risk:g}% = {_idr_short(s.risk_budget_idr)}"]
    if s.shares == 0:
        lines.append(f"\n❌ Not even one {'lot' if s.lot > 1 else 'share'} fits: one "
                     f"{'lot' if s.lot > 1 else 'share'} would lose {_idr_short(s.one_lot_risk_idr)} at the stop. "
                     "Use a closer stop, a higher risk %, or skip this one.")
        if s.lot == 1 and s.fractional > 0:
            lines.append(f"If your broker allows fractional shares: {s.fractional:.2f} shares "
                         f"({_idr_short(s.fractional * entry * s.position_idr_per_unit)}).")
        return "\n".join(lines)
    qty = f"{s.shares // s.lot:,} lots ({s.shares:,} shares)" if s.lot > 1 else f"{s.shares:,} shares"
    lines += [
        f"\nBuy <b>{qty}</b>",
        f"Position {_price(s.position, ccy)} {ccy} = {_idr_short(s.position_idr)} · {s.weight_pct:.1f}% of capital",
        f"If the stop is hit: −{_idr_short(s.loss_at_stop_idr)} ({s.loss_pct:.2f}% of capital)",
    ]
    if s.capped:
        lines.append(f"ℹ️ Capped by the {max_pos:g}% max position; risk alone would allow more.")
    lines.append("<i>Fees not included (IDX ~0.15% buy, ~0.25% sell). A stop only limits losses if you "
                 "actually sell there; gaps can go through it. Research only, not investment advice.</i>")
    return "\n".join(lines)


# --- portfolio ---------------------------------------------------------------------------
def _idr_signed(v: float | None) -> str:
    if v is None:
        return "n/a"
    return ("+" if v >= 0 else "−") + _idr_short(abs(v))


def parse_date(text: str, today=None):
    """'2026-11-20', '20/11/2026', '20/11' (this year), 'today', 'yesterday' -> 'YYYY-MM-DD'.
    None if it isn't a date, is in the future, or is before 2000."""
    from datetime import date, datetime, timedelta

    today = today or date.today()
    s = text.strip().lower()
    if s in ("today", "hari ini"):
        d = today
    elif s in ("yesterday", "kemarin"):
        d = today - timedelta(days=1)
    else:
        d = None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                pass
        if d is None:
            m = re.fullmatch(r"(\d{1,2})/(\d{1,2})", s)
            if not m:
                return None
            try:
                d = date(today.year, int(m.group(2)), int(m.group(1)))
            except ValueError:
                return None
            if d > today:                       # '28/12' typed in January means last year
                d = d.replace(year=d.year - 1)
    if d > today or d.year < 2000:
        return None
    return d.isoformat()


def record_transaction(db, kind: str, symbol: str, qty: str = "", price: str = "", day: str = "",
                       fee: str = "", amount: str = "", today=None) -> tuple[int, str]:
    """Check and save a BUY, SELL or DIV, typed in Telegram or the dashboard.
    qty is in lots for IDX and shares for US. Returns (id, plain-text summary);
    raises PortfolioError with a readable reason."""
    from app.analytics import portfolio as pf

    kind = kind.upper()
    if kind not in ("BUY", "SELL", "DIV"):
        raise pf.PortfolioError("kind must be buy, sell or dividend")
    inst = db.get_instrument(symbol) if symbol else None
    if inst is None:
        raise pf.PortfolioError(f"{symbol.upper()[:15]} isn't on your watchlist; add it first "
                                f"(/watch {symbol.upper()[:15]} idx|us)")
    market, ccy, sym = inst["market"], inst["currency"], inst["symbol"]
    date_s = parse_date(day, today) if day.strip() else parse_date("today", today)
    if date_s is None:
        raise pf.PortfolioError("date must be like 2026-11-20 or 20/11, and not in the future")
    fx = 1.0 if ccy == "IDR" else (db.get_fx_on("USDIDR", date_s) or db.get_latest_fx("USDIDR"))
    if fx is None:
        raise pf.PortfolioError("no USD/IDR rate stored yet; run /digest us once first")
    row = {"symbol": sym, "date": date_s, "kind": kind, "market": market, "currency": ccy,
           "asset_type": inst["asset_type"], "fx_idr": fx, "shares": 0.0, "price": 0.0,
           "fee": 0.0, "amount": None}

    if kind == "DIV":
        a = parse_number(amount[:30]) if amount else None
        if a is None or a <= 0:
            raise pf.PortfolioError(f"dividend amount must be the cash you received in {ccy}, e.g. 1.2jt")
        row["amount"] = a
        summary = f"dividend {sym} {_price(a, ccy)} {ccy} on {date_s}"
    else:
        q = parse_number(qty[:30]) if qty else None
        p = parse_number(price[:30]) if price else None
        if q is None or q <= 0 or p is None or p <= 0:
            unit = "lots" if market == "IDX" else "shares"
            raise pf.PortfolioError(f"quantity ({unit}) and price must be positive numbers")
        if market == "IDX" and q != int(q):
            raise pf.PortfolioError("IDX quantity is in whole lots (1 lot = 100 shares)")
        shares = q * pf.LOT.get(market, 1)
        if fee.strip():
            f = parse_number(fee[:30])
            if f is None or f < 0:
                raise pf.PortfolioError("fee must be an amount, e.g. 12500 (or 0)")
            fee_note = ""
        else:
            f = pf.default_fee(market, kind, shares, p)
            fee_note = f" (estimated {pf.DEFAULT_FEE_PCT[(market, kind)]:g}%)" if f else ""
        row.update(shares=shares, price=p, fee=f)
        summary = (f"{kind.lower()} {sym} {pf._qty(shares, market)} @ {_price(p, ccy)} {ccy} on {date_s}, "
                   f"fee {_price(f, ccy)}{fee_note}")
    pf.check_ledger(db, extra=row)
    tid = db.add_transaction(sym, date_s, kind, row["shares"], row["price"], row["fee"], row["amount"],
                             row["fx_idr"])
    pf.sync_holding_flags(db, sym)
    return tid, summary


def delete_transaction(db, txn_id: int) -> str:
    from app.analytics import portfolio as pf

    t = db.get_transaction(txn_id)
    if t is None:
        raise pf.PortfolioError(f"no transaction #{txn_id}")
    pf.check_ledger(db, without=txn_id)
    db.delete_transaction(txn_id)
    pf.sync_holding_flags(db, t["symbol"])
    return f"Deleted #{txn_id} ({t['kind'].lower()} {t['symbol']} on {t['date']})."


def _trade_cmd(db, kind: str, args: list[str], today=None) -> str:
    from app.analytics.portfolio import PortfolioError

    verb = kind.lower()
    usage = (f"Usage: /{verb} SYMBOL QTY PRICE [DATE] [fee=AMOUNT]\n"
             f"QTY is lots for IDX, shares for US (fractions OK). DATE defaults to today.\n"
             f"Example: /{verb} BBCA 10 8950 20/11   ·   /{verb} VOO 1.5 540.20 2026-11-20 fee=0\n"
             "Without fee= an IDX fee of ~0.15% (buy) / ~0.25% (sell) is assumed.")
    if len(args) < 3:
        return usage
    fee, rest = "", []
    for a in args[3:]:
        if a.lower().startswith("fee="):
            fee = a[4:]
        else:
            rest.append(a)
    if len(rest) > 1:
        return usage
    try:
        tid, summary = record_transaction(db, kind, args[0], args[1], args[2], rest[0] if rest else "",
                                          fee, today=today)
    except PortfolioError as e:
        return f"❌ {escape(str(e))}\n\n{usage}"
    return f"✅ #{tid}: {escape(summary)}\nSee /portfolio · undo with /deltxn {tid}"


def cmd_buy(db, args: list[str], today=None) -> str:
    return _trade_cmd(db, "BUY", args, today)


def cmd_sell(db, args: list[str], today=None) -> str:
    return _trade_cmd(db, "SELL", args, today)


def cmd_div(db, args: list[str], today=None) -> str:
    from app.analytics.portfolio import PortfolioError

    usage = ("Usage: /div SYMBOL AMOUNT [DATE]\nAMOUNT is the cash you received after tax, in the "
             "stock's currency.\nExample: /div BBRI 1.25jt 2026-11-20")
    if len(args) not in (2, 3):
        return usage
    try:
        tid, summary = record_transaction(db, "DIV", args[0], day=args[2] if len(args) == 3 else "",
                                          amount=args[1], today=today)
    except PortfolioError as e:
        return f"❌ {escape(str(e))}\n\n{usage}"
    return f"✅ #{tid}: {escape(summary)}\nUndo with /deltxn {tid}"


def cmd_txns(db, args: list[str] | None = None) -> str:
    from app.analytics.portfolio import _qty

    sym = args[0].upper()[:15] if args else None
    txns = db.get_transactions(sym)
    if not txns:
        return ("No transactions" + (f" for {escape(sym)}" if sym else "") +
                " yet. Record one: /buy BBCA 10 8950 20/11")
    lines = [f"<b>Transactions</b>{' · ' + escape(sym) if sym else ''} (newest first)"]
    for t in reversed(txns[-25:]):
        ccy = t["currency"] or "IDR"
        if t["kind"] == "DIV":
            what = f"dividend {_price(t['amount'], ccy)} {ccy}"
        else:
            what = (f"{t['kind'].lower()} {_qty(t['shares'], t['market'] or 'IDX')} @ {_price(t['price'], ccy)}"
                    f"{' · fee ' + _price(t['fee'], ccy) if t['fee'] else ''}")
        lines.append(f"#{t['id']} {t['date']} <b>{escape(t['symbol'])}</b> {what}")
    if len(txns) > 25:
        lines.append(f"<i>…and {len(txns) - 25} older. /txns SYMBOL shows one stock.</i>")
    lines.append("Delete one: /deltxn ID")
    return "\n".join(lines)


def cmd_deltxn(db, args: list[str]) -> str:
    from app.analytics.portfolio import PortfolioError

    if len(args) != 1 or not args[0].lstrip("#").isdigit():
        return "Usage: /deltxn ID   (IDs are in /txns)"
    try:
        return "✅ " + escape(delete_transaction(db, int(args[0].lstrip("#"))))
    except PortfolioError as e:
        return f"❌ {escape(str(e))}"


def cmd_portfolio(db, args: list[str] | None = None) -> str:
    from app.analytics import portfolio as pf

    port = pf.build(db)
    if not port.positions and not port.closed:
        msg = ("No positions recorded yet. Record what you own:\n/buy BBCA 10 8950 20/11   (IDX in lots)\n"
               "/buy VOO 1.5 540.20 2026-11-20   (US in shares)")
        if port.untracked:
            msg += f"\n\nMarked as holdings: {escape(', '.join(port.untracked))}"
        if port.warnings:
            msg += "\n\n⚠️ " + escape(" ".join(port.warnings))
        return msg

    head = "<b>Portfolio</b>" + (f" · closes to {port.as_of:%d %b %Y}" if port.as_of is not None else "")
    lines = [head, f"Value <b>{_idr_short(port.value_idr)}</b>"
             + (f" · today {_idr_signed(port.day_change_idr)} ({_fmt(port.day_change_pct, '%', 1, True)})"
                if port.day_change_idr is not None else "")]
    lines.append(f"Unrealised {_idr_signed(port.unrealised_idr)} ({_fmt(port.unrealised_pct, '%', 1, True)}) · "
                 f"realised {_idr_signed(port.realised_idr)} · dividends {_idr_short(port.income_idr)}")
    if port.invested_pct is not None:
        lines.append(f"Invested: {port.invested_pct:.0f}% of your {_idr_short(port.capital_idr)} capital")

    header = ("SYM", "QTY", "AVG", "LAST", "P/L", "WT")
    rows = [header]
    for p in port.positions:
        qty = f"{p.lots:,.0f}lot" if p.market == "IDX" else f"{p.shares:,.4g}sh"
        pl = "n/a" if p.unrealised_pct is None else f"{p.unrealised_pct:+.1f}%"
        wt = "" if p.weight is None else f"{p.weight:.0f}%"
        rows.append((p.symbol, qty, _price(p.avg_cost, p.currency), _price(p.close, p.currency), pl, wt))
    if len(rows) > 1:
        widths = [max(len(r[i]) for r in rows) for i in range(6)]
        text = "\n".join(" ".join(r[i].ljust(widths[i]) if i < 2 else r[i].rjust(widths[i]) for i in range(6))
                         for r in rows)
        lines.append("<pre>" + escape(text) + "</pre>")

    for p in port.positions:
        if p.fx_effect_idr is not None and abs(p.fx_effect_idr) >= 1 and p.unrealised_idr_pct is not None:
            lines.append(f"{escape(p.symbol)} in IDR {p.unrealised_idr_pct:+.1f}% "
                         f"({_idr_signed(p.unrealised_idr)}, of which the rupiah {_idr_signed(p.fx_effect_idr)})")
    if port.by_sector:
        lines.append("Sectors: " + escape(" · ".join(f"{n} {w:.0f}%" for n, w in port.by_sector)))
    if len(port.by_currency) > 1:
        lines.append("Currency: " + " · ".join(f"{n} {w:.0f}%" for n, w in port.by_currency))
    for w in port.warnings:
        lines.append(f"⚠️ {escape(w)}")
    if port.missing_prices:
        lines.append(f"No stored price for {escape(', '.join(port.missing_prices))}; left out of the totals.")
    if port.closed:
        lines.append("Closed: " + escape(" · ".join(f"{p.symbol} {_idr_signed(p.realised_idr + p.income_idr)}"
                                                    for p in port.closed)))
    if port.untracked:
        lines.append(f"Marked as holdings without transactions: {escape(', '.join(port.untracked))} "
                     "(record them with /buy)")
    lines.append("<i>Average-cost method, fees included. IDR cost uses the rate on each buy day. "
                 "Research only, not investment advice.</i>")
    return "\n".join(lines)


def cmd_price(db, args: list[str], provider=None, now=None) -> str:
    from app.analytics import portfolio as pf
    from app.analytics.zones import distance_pct, state_of

    if len(args) != 1:
        return "Usage: /price SYMBOL   e.g. /price BBCA"
    inst, err = _watched(db, args[0])
    if err:
        return err
    _latest_close(db, inst, provider, now)
    hist = db.get_price_history(inst.symbol, days=260)
    if hist.empty:
        return f"No prices stored for {escape(inst.symbol)} yet."
    ccy, close = inst.currency, float(hist["Close"].iloc[-1])
    prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
    chg = (close / prev - 1) * 100 if prev else None
    lines = [f"<b>{escape(inst.symbol)}</b> {_price(close, ccy)} {ccy} "
             f"({_fmt(chg, '%', 2, True)}) · close {hist.index[-1]:%a %d %b}"]
    lo, hi = hist["Low"].min(), hist["High"].max()
    if lo == lo and hi == hi:                   # not NaN
        lines.append(f"52-week range {_price(float(lo), ccy)} – {_price(float(hi), ccy)}")
    zs = db.get_zones(inst.symbol)
    if zs:
        z = min(zs, key=lambda z: abs(distance_pct(close, z["low"], z["high"]) or 0))
        st = state_of(close, z["low"], z["high"])
        where = {"inside": "inside it", "below": "below it"}.get(
            st, f"needs a {abs(distance_pct(close, z['low'], z['high'])):.1f}% fall to reach it")
        lines.append(f"Buy zone #{z['id']} {_price(z['low'], ccy)}–{_price(z['high'], ccy)}: {where}")
    txns = db.get_transactions(inst.symbol)
    p = pf.replay(txns, db.get_latest_fx("USDIDR")).get(inst.symbol) if txns else None
    if p and p.open:
        value = p.shares * close
        pl = value - p.cost
        qty = f"{p.lots:,.0f} lots" if inst.market == "IDX" else f"{p.shares:,.4g} shares"
        lines.append(f"You hold {qty} @ {_price(p.avg_cost, ccy)} avg: {_price(value, ccy)} {ccy}, "
                     f"{'+' if pl >= 0 else '−'}{_price(abs(pl), ccy)} ({pl / p.cost * 100:+.1f}%)")
    return "\n".join(lines)


# --- dividends ---------------------------------------------------------------------------
def _div_qty(e) -> str:
    return f"{e.lots:,.0f} lots" if e.market == "IDX" else f"{e.shares:,.4g} shares"


def div_line(e, with_cum: bool = True) -> str:
    """One dividend event as a line of HTML, shared by /dividends and the digests."""
    ccy = e.currency
    amt = "amount not announced yet" if e.per_share is None else (
        f"{_price(e.per_share, ccy)}/share ≈ {_price(e.gross, ccy)} {ccy} for your {_div_qty(e)}"
        f" (≈ {_price(e.net, ccy)} after tax)")
    if e.status == "expected":
        return f"• <b>{escape(e.symbol)}</b> ~{e.ex_date:%d %b}? {amt} · <i>estimate: {escape(e.basis)}</i>"
    cum = f" · own it by the close of {e.cum_date:%a %d %b}" if with_cum and e.status == "announced" else ""
    return f"• <b>{escape(e.symbol)}</b> ex {e.ex_date:%a %d %b}: {amt}{cum}"


def record_hint(e) -> str:
    if e.net is None:
        return f"/div {e.symbol} AMOUNT"
    return f"/div {e.symbol} {e.net:.0f}" if e.currency == "IDR" else f"/div {e.symbol} {e.net:.2f}"


def cmd_calendar(db, args: list[str] | None = None, today=None) -> str:
    """Next 14 days (or /calendar 30): macro events, market holidays, your dividends."""
    from datetime import date

    from app.analytics import dividends as dv
    from app.analytics import macro_calendar as mc

    today = today or date.today()
    days = 14
    if args:
        if not args[0].isdigit() or not 1 <= int(args[0]) <= 120:
            return "Usage: /calendar [DAYS]   e.g. /calendar 30 (up to 120)"
        days = int(args[0])
    events, _ = mc.load()
    lines = [f"<b>📅 Next {days} days</b>"]
    divs = [e for e in dv.calendar(db, today=today, days=days)["upcoming"]]
    rows = [(e.day, f"{e.icon} {escape(e.title)}") for e in mc.upcoming(events, today, days)]
    rows += [(e.ex_date, ("💰 ~" if e.status == "expected" else "💰 ") + f"{escape(e.symbol)} ex-dividend"
              + (f" ({_price(e.per_share, e.currency)}/share)" if e.per_share else "")) for e in divs]
    if not rows:
        lines.append("Nothing scheduled.")
    for d, text in sorted(rows, key=lambda r: r[0]):
        lines.append(f"{d:%a %d %b} {text}")
    end = mc.covered_until(events)
    if mc.needs_update(events, today):
        lines.append(f"<i>⚠️ The macro calendar file runs out on {end:%d %b %Y}. Update "
                     f"app/reference/calendar.json with next year's schedules.</i>" if end else
                     "<i>⚠️ No macro calendar file found (app/reference/calendar.json).</i>")
    lines.append("<i>~ = estimated from last year. /dividends has amounts and your expected cash.</i>")
    return "\n".join(lines)


def cmd_dividends(db, args: list[str] | None = None, provider=None, today=None) -> str:
    from app.analytics import dividends as dv

    try:
        failed = dv.refresh(db, provider=provider, today=today)
    except Exception:
        failed = ["all"]
    cal = dv.calendar(db, today=today)
    lines = [f"<b>💰 Dividends</b> · next {dv.LOOKAHEAD_DAYS} days, your holdings"]
    if cal["upcoming"]:
        lines += [div_line(e) for e in cal["upcoming"]]
    else:
        lines.append("Nothing announced or expected.")
    if cal["to_record"]:
        lines.append("\n<b>Went ex while you held it; record the cash when it arrives</b>")
        for e in cal["to_record"]:
            lines.append(f"{div_line(e, with_cum=False)}\n  <code>{escape(record_hint(e))}</code>")
    income = [r for r in dv.yearly_income(db, today=today) if r["gross_idr"] > 0]
    if income:
        total = sum(r["gross_idr"] for r in income)
        net = sum(r["net_idr"] for r in income)
        lines.append(f"\nLast 12 months' dividends on what you hold now: {_idr_short(total)} a year "
                     f"(≈ {_idr_short(net)} after tax)")
        lines.append(escape(" · ".join(f"{r['symbol']} {_idr_short(r['gross_idr'])}" for r in income[:6])))
    if failed:
        lines.append(f"<i>Couldn't refresh from Yahoo: {escape(', '.join(failed))}; showing stored data.</i>")
    lines.append("<i>'~' dates are last year's, not announced. Tax: 10% IDX, 15% US with a W-8BEN. "
                 "Research only, not investment advice.</i>")
    return "\n".join(lines)

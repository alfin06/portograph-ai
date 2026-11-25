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

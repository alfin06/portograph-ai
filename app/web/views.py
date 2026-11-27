"""Data for each web page, read from the database only (no network), so pages load fast
and work while Yahoo or the VPN is down. Actions that need Yahoo (rebuilding valuation
bands, ETF profiles) are explicit buttons that go through the same analytics code as
the Telegram bot, so the numbers match."""
import ast
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from app.analytics import sentiment
from app.analytics import valuation as val
from app.analytics.fx_returns import PERIOD_NAMES, compare_closes
from app.analytics.screener import FIELDS, RuleError, collect_metrics, parse_rule, run_screens
from app.analytics.sizing import DEFAULT_MAX_POSITION_PCT, DEFAULT_RISK_PCT
from app.analytics.snapshot import build_snapshot, market_snapshots
from app.analytics.zones import distance_pct, state_of
from app.symbols import Instrument
from core import config

log = logging.getLogger(__name__)

MARKETS = ("IDX", "US")
PRICE_PCTS = (10, 25, 50, 75, 90)   # band lines drawn on the chart, in "price percentile" terms


def _d(ts) -> str:
    return f"{pd.Timestamp(ts):%Y-%m-%d}"


def _num(v):
    return None if v is None or pd.isna(v) else float(v)


def zone_rows(db, zones: list[dict]) -> list[dict]:
    out = []
    for z in zones:
        hist = db.get_price_history(z["symbol"], days=5)
        close = None if hist.empty else float(hist["Close"].iloc[-1])
        row = {**z, "close": close, "state": None, "distance": None}
        if close is not None:
            row["state"] = state_of(close, z["low"], z["high"])
            row["distance"] = distance_pct(close, z["low"], z["high"])
        out.append(row)
    return out


# --- overview ----------------------------------------------------------------------
def overview(db) -> dict:
    today = date.today().isoformat()
    markets = []
    for m in MARKETS:
        snaps = market_snapshots(db, m)
        try:
            mood = sentiment.from_db(db, m)
        except Exception:
            mood = None
        screens = [s for s in run_screens(db, m, snaps, today, record=False) if s.hits or s.error]
        markets.append({
            "market": m,
            "snaps": sorted(snaps, key=lambda s: (not s.is_holding, s.symbol)),
            "as_of": max((s.date for s in snaps), default=None),
            "mood": mood,
            "screens": screens,
            "zones": [z for z in zone_rows(db, db.get_zones(market=m)) if z["state"] in ("inside", "below")],
        })
    from app.analytics import portfolio as pf
    port = pf.build(db)
    return {"markets": markets, "usdidr": _fx(db), "port": port if port.positions else None}


def _fx(db):
    s = db.get_fx_history("USDIDR", days=2)
    if s.empty:
        return None
    chg = (s.iloc[-1] / s.iloc[0] - 1) * 100 if len(s) == 2 else None
    return {"rate": float(s.iloc[-1]), "chg": chg, "date": s.index[-1]}


# --- one stock ---------------------------------------------------------------------
def _sma(close: pd.Series, n: int) -> list[dict]:
    s = close.rolling(n).mean().dropna()
    return [{"time": _d(t), "value": round(float(v), 4)} for t, v in s.items()]


def band_prices(profile: dict, metric: str) -> dict[int, float] | None:
    """Today's price that would put `metric` at each price percentile (10 = cheapest 10%).
    For yield the scale is flipped: a high yield is the cheap side."""
    b = profile["bands"].get(metric)
    cur = profile["current"]
    if not b:
        return None
    if metric == "yield":
        d = cur.get("ttm_div") or 0
        return {p: d / (val.at(b, 100 - p) / 100) for p in PRICE_PCTS} if d > 0 else None
    per_share = cur.get("bvps" if metric == "pb" else "eps")
    if not per_share or per_share <= 0:
        return None
    return {p: val.at(b, p) * per_share for p in PRICE_PCTS}


def stock(db, symbol: str) -> dict | None:
    inst = db.get_instrument(symbol)
    if inst is None:
        return None
    hist = db.get_price_history(inst["symbol"])
    snap = build_snapshot(inst, hist.tail(320)) if not hist.empty else None
    candles, volume = [], []
    for t, r in hist.iterrows():
        c = _num(r.Close)
        if c is None:
            continue
        o, h, lo = (_num(r.Open) or c), (_num(r.High) or c), (_num(r.Low) or c)
        candles.append({"time": _d(t), "open": o, "high": max(h, o, c), "low": min(lo, o, c), "close": c})
        volume.append({"time": _d(t), "value": _num(r.Volume) or 0.0})

    profile, readings, bands, roe_warn, stale = None, [], {}, None, False
    if inst["asset_type"] != "ETF":
        profile = db.get_valuation_bands(inst["symbol"])
        stale = val.is_stale(profile)
        if profile and snap:
            readings = val.assess(profile, snap.close)
            for m in val.METRICS:
                prices = band_prices(profile, m)
                if prices:
                    bands[m] = {"label": val.LABEL[m], "prices": prices}
            roe_warn = val.roe_warning(profile.get("roe", []))

    zones = zone_rows(db, db.get_zones(symbol=inst["symbol"]))
    cap = db.get_setting("capital_idr")
    return {
        "inst": inst, "snap": snap, "fund": db.get_latest_fundamentals(inst["symbol"]),
        "chart": {
            "candles": candles, "volume": volume,
            "sma50": _sma(hist["Close"], 50) if not hist.empty else [],
            "sma200": _sma(hist["Close"], 200) if not hist.empty else [],
            "bands": bands,
            "zones": [{"id": z["id"], "low": z["low"], "high": z["high"]} for z in zones],
            "idr": inst["currency"] == "IDR",
        },
        "profile": profile, "readings": readings, "roe_warn": roe_warn, "valuation_stale": stale,
        "zones": zones, "capital": None if cap is None else float(cap),
        "default_risk": DEFAULT_RISK_PCT, "default_max": DEFAULT_MAX_POSITION_PCT,
        "suggest_zone": _suggest_zone(readings, snap),
    }


def _suggest_zone(readings, snap):
    if not readings or snap is None:
        return None
    r = readings[0]
    if snap.close <= r.price_cheap:
        return None
    return {"low": r.price_deep, "high": r.price_cheap, "note": f"{val.LABEL[r.metric]} cheapest quarter"}


def refresh_valuation(db, symbol: str, provider=None, fx_provider=None) -> str | None:
    """Rebuild the bands from Yahoo. Returns the as-of date of the bands now stored
    (the old copy's date if Yahoo failed), or None if there are none."""
    inst = db.get_instrument(symbol)
    if inst is None or inst["asset_type"] == "ETF":
        return None
    i = Instrument(inst["symbol"], inst["market"], inst["asset_type"], inst["currency"])
    p = val.get_profile(db, i, provider=provider, fx_provider=fx_provider, force=True)
    return None if p is None else p.get("as_of")


# --- screens -----------------------------------------------------------------------
def screens(db) -> dict:
    today = date.today().isoformat()
    snaps = {m: market_snapshots(db, m) for m in MARKETS}
    rows = []
    for sc in db.get_screens(active_only=False):
        hits = []
        for m in ([sc["market"]] if sc["market"] else MARKETS):
            for r in run_screens(db, m, snaps[m], today, record=False):
                if r.name == sc["name"]:
                    hits += r.hits
        rows.append({**sc, "hits": sorted(set(hits))})
    return {"screens": rows, "fields": FIELDS}


def run_rule(db, text: str) -> dict:
    try:
        rule = parse_rule(text)
    except RuleError as e:
        return {"error": str(e), "rule": text}
    snaps = [s for m in MARKETS for s in market_snapshots(db, m)]
    metrics = collect_metrics(db, snaps)
    hits = sorted(sym for sym, m in metrics.items() if rule.matches(m))
    fields = []
    for node in ast.walk(rule.tree):
        if isinstance(node, ast.Name) and node.id not in fields and node.id != "holding":
            fields.append(node.id)
    return {"rule": rule.text, "hits": [(s, metrics[s]) for s in hits], "fields": fields[:5],
            "total": len(metrics), "error": None}


# --- compare -----------------------------------------------------------------------
def compare(db, symbols: list[str], period: str) -> dict:
    period = period if period in PERIOD_NAMES else "1y"
    found, problems = [], []
    for raw in symbols[:8]:
        inst = db.get_instrument(raw)
        if inst is None or not inst["active"]:
            problems.append(f"{raw.upper()} isn't on your watchlist")
            continue
        hist = db.get_price_history(inst["symbol"])
        if hist.empty:
            problems.append(f"{inst['symbol']}: no prices stored yet")
            continue
        found.append((inst["symbol"], inst["currency"], hist["Close"]))
    if not found:
        return {"results": [], "problems": problems, "series": {}, "period": period}
    cmp = compare_closes(found, db.get_fx_history("USDIDR"), period)
    series = {sym: {k: [{"time": _d(t), "value": round(float(v), 3)} for t, v in s.items()]
                    for k, s in g.items()} for sym, g in cmp.growth.items()}
    return {"results": cmp.results, "problems": problems + cmp.problems, "series": series,
            "period": period, "fx": cmp.results[0].fx if cmp.results else None}


# --- ETFs --------------------------------------------------------------------------
def etf_page(db, a: str | None, b: str | None, funds_provider=None, provider=None) -> dict:
    from app.analytics import etf
    from app.commands import JUTA_100, _pretty_sector, _us_close

    out = {"a": a, "b": b, "profiles": [], "overlap": None, "problems": [],
           "watched": [i["symbol"] for i in db.get_instruments() if i["asset_type"] == "ETF"]}
    profiles = {}
    for sym in [s for s in (a, b) if s]:
        p = etf.get_profile(db, sym, provider=funds_provider)
        if p is None:
            out["problems"].append(f"No ETF data for {sym}. Is it a US-listed ETF?")
            continue
        profiles[sym] = p
        er = p.get("expense_ratio")
        out["profiles"].append({
            "symbol": sym, **p,
            "fee_year": etf.annual_fee(er, JUTA_100), "fee_drag": etf.fee_drag(er, JUTA_100),
            "sector_list": [(_pretty_sector(k), v) for k, v in
                            sorted((p.get("sectors") or {}).items(), key=lambda kv: -kv[1])],
        })
    if a and b and a in profiles and b in profiles:
        ov = etf.compare(profiles[a], profiles[b], _us_close(db, a, provider), _us_close(db, b, provider))
        out["overlap"] = {"ov": ov, "label": etf.similarity_label(ov)}
    return out


# --- status ------------------------------------------------------------------------
def status(db, now=None) -> dict:
    from app.run import control

    now = now or datetime.now(timezone.utc)
    runs = {r["job"]: r for r in db.get_last_job_runs()}
    digests = []
    for m in MARKETS:
        r = runs.get(f"digest_{m}")
        ago = None if r is None else (now - datetime.fromisoformat(r["ran_at"])).total_seconds() / 3600
        digests.append({"market": m, "run": r, "hours_ago": ago})
    prices = []
    for m in MARKETS:
        dates = [h.index[-1] for h in (db.get_price_history(i["symbol"], days=5)
                                      for i in db.get_instruments(market=m)) if not h.empty]
        prices.append({"market": m, "to": max(dates) if dates else None})
    backups = sorted(Path(config.BACKUP_DIR).glob("portograph-*.db"))
    procs = control("status")
    return {
        "digests": digests, "prices": prices,
        "backup": {"name": backups[-1].name, "kb": backups[-1].stat().st_size / 1024,
                   "count": len(backups)} if backups else None,
        "processes": procs.splitlines() if procs else None,
    }


# --- portfolio -----------------------------------------------------------------------
def portfolio(db, today: date | None = None) -> dict:
    from app.analytics import portfolio as pf
    port = pf.build(db)
    txns = list(reversed(db.get_transactions()))
    history = [{"time": h["date"], "value": round(h["value_idr"]), "cost": round(h["cost_idr"])}
               for h in db.get_portfolio_history()]
    bench, bench_series = None, {}
    try:
        from app.analytics import benchmark
        bench = benchmark.from_db(db, port)
        if bench is not None:
            bench_series = {label: {"idr": [{"time": f"{d:%Y-%m-%d}", "value": round(float(v), 2)}
                                            for d, v in s.items()]}
                            for label, s in bench.growth.items()}
    except Exception:   # the page still works without the comparison
        log.exception("benchmark failed")
    return {"port": port, "history": history, "bench": bench, "bench_series": bench_series,
            "txns": txns[:100], "more_txns": max(0, len(txns) - 100),
            "watched": [i["symbol"] for i in db.get_instruments()],
            "today": (today or date.today()).isoformat(), "lot": pf.LOT,
            "fee_pct": pf.DEFAULT_FEE_PCT, "max_stock": pf.MAX_STOCK_PCT, "max_sector": pf.MAX_SECTOR_PCT}


# --- calendar ------------------------------------------------------------------------
CAL_DAYS = 90


def calendar_page(db, today: date | None = None) -> dict:
    from app.analytics import dividends as dv
    from app.analytics import macro_calendar as mc
    today = today or date.today()
    cal = dv.calendar(db, today=today, days=CAL_DAYS)
    events, meta = mc.load()
    months: dict[str, list] = {}
    rows = [{"day": e.day, "icon": e.icon, "text": e.title, "kind": e.kind, "div": None}
            for e in mc.upcoming(events, today, CAL_DAYS)]
    rows += [{"day": e.ex_date, "icon": "💰", "text": f"{e.symbol} ex-dividend", "kind": "dividend", "div": e}
             for e in cal["upcoming"]]
    for r in sorted(rows, key=lambda r: (r["day"], r["kind"] != "dividend")):
        months.setdefault(f"{r['day']:%B %Y}", []).append(r)
    income = [r for r in dv.yearly_income(db, today=today) if r["gross_idr"] > 0]
    fetched = [db.get_setting(f"dividends_fetched:{i['symbol']}") for i in db.get_instruments() if i["is_holding"]]
    return {"months": months, "upcoming": cal["upcoming"], "to_record": cal["to_record"], "income": income,
            "income_total": sum(r["gross_idr"] for r in income), "income_net": sum(r["net_idr"] for r in income),
            "today": today, "days": CAL_DAYS, "notes": meta.get("notes", []), "sources": meta.get("sources", {}),
            "covered_until": mc.covered_until(events), "stale": mc.needs_update(events, today),
            "divs_fetched": min((f for f in fetched if f), default=None), "tax": dv.TAX_PCT}

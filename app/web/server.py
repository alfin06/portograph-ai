"""PortoGraph web dashboard.

    python -m app.web          # serve on WEB_HOST:WEB_PORT (default http://127.0.0.1:8000)

Usually started by `python -m app.run` together with the bot and worker. Only this
computer can open it unless WEB_HOST is changed, and then WEB_PASSWORD is required.
"""
import logging
import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.commands import _idr_short, parse_number
from app.web import security as sec
from app.web import views
from app.web.security import AuthGuard
from core import config

log = logging.getLogger("web")
HERE = Path(__file__).parent

# Windows often maps .js to text/plain in the registry, and with X-Content-Type-Options:
# nosniff the browser then refuses to run our scripts. Set the types explicitly.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


# --- formatting used by the templates ---------------------------------------------
def f_price(v, currency="IDR"):
    if v is None:
        return "–"
    return f"{v:,.0f}" if currency == "IDR" else f"{v:,.2f}"


def f_pct(v, digits=1, signed=True):
    if v is None:
        return "–"
    return f"{v:+,.{digits}f}%" if signed else f"{v:,.{digits}f}%"


def f_num(v, digits=1, suffix=""):
    return "–" if v is None else f"{v:,.{digits}f}{suffix}"


def f_date(v, fmt="%a %d %b %Y"):
    return "–" if v is None else v.strftime(fmt)


def tone(v):
    """CSS class for a signed number."""
    return "" if v is None or v == 0 else ("up" if v > 0 else "down")


def build_templates() -> Jinja2Templates:
    t = Jinja2Templates(directory=str(HERE / "templates"))
    # ?v= on our own CSS/JS so the browser picks up a new version after an update
    files = [*(HERE / "static").rglob("*.*"), Path(__file__), HERE / "security.py"]
    t.env.globals["v"] = str(int(max(p.stat().st_mtime for p in files)))
    t.env.filters.update(price=f_price, pct=f_pct, num=f_num, date=f_date, idr=_idr_short, tone=tone)
    return t


# --- app ----------------------------------------------------------------------------
def create_app(db=None, funds_provider=None, val_provider=None, fx_provider=None, provider=None,
               extra_hosts: list[str] | None = None) -> FastAPI:
    """db and the providers are injectable for tests; by default the real DB and Yahoo.
    extra_hosts adds accepted Host names (tests use 'testserver')."""
    app = FastAPI(title="PortoGraph", docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    templates = build_templates()
    state = {"db": db}
    auth = AuthGuard()

    def get_db():
        if state["db"] is None:
            from database.db_manager import DBManager
            state["db"] = DBManager()
        return state["db"]

    def deny(status: int, text: str, headers: dict | None = None):
        return Response(text, status_code=status, media_type="text/plain",
                        headers={**sec.HEADERS, "Cache-Control": "no-store", **(headers or {})})

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Hosts are re-read per request so a .env change is picked up on restart only,
        # but tests can switch WEB_PASSWORD / WEB_ALLOWED_HOSTS freely
        if not sec.host_ok(request.headers.get("host"), sec.allowed_hosts(extra_hosts)):
            return deny(400, "Unknown host. Open the dashboard at http://127.0.0.1:%d" % config.WEB_PORT)
        if sec.cross_site_blocked(request.method, request.headers):
            return deny(403, "Cross-site request blocked")
        client = request.client.host if request.client else "?"
        if config.WEB_PASSWORD:
            if auth.locked(client):
                return deny(429, "Too many wrong passwords. Try again in 15 minutes.")
            if not auth.check(client, request.headers.get("authorization")):
                return deny(401, "Password required", {"WWW-Authenticate": 'Basic realm="PortoGraph", charset="UTF-8"'})
        if request.method == "POST":
            if not sec.same_origin(request.headers):
                return deny(403, "Cross-site request blocked")
            size = request.headers.get("content-length")
            if size is None or not size.isdigit() or int(size) > sec.MAX_BODY:
                return deny(413, "Request too large")
        elif request.method not in ("GET", "HEAD"):
            return deny(405, "Method not allowed")
        response = await call_next(request)
        for k, v in sec.HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"   # revalidate (ETag), so fixes always load
        else:
            response.headers["Cache-Control"] = "no-store"   # pages hold your holdings and capital
        return response

    def page(request, name, status_code=200, **ctx):
        msg = request.query_params.get("msg")
        ctx.setdefault("msg", msg[:sec.MAX_MSG] if msg else None)
        return templates.TemplateResponse(request, name, ctx, status_code=status_code)

    def bad_symbol(request, symbol):
        return page(request, "notfound.html", status_code=404, nav=None,
                    what=f"{symbol[:20]!r} isn't a valid symbol.")

    def back(url: str, msg: str | None = None):
        if msg:
            url += ("&" if "?" in url else "?") + "msg=" + quote(msg)
        return RedirectResponse(url, status_code=303)

    # pages ----------------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request):
        return page(request, "overview.html", nav="overview", **views.overview(get_db()))

    @app.get("/stock/{symbol}", response_class=HTMLResponse)
    def stock(request: Request, symbol: str):
        if not sec.valid_symbol(symbol):
            return bad_symbol(request, symbol)
        data = views.stock(get_db(), symbol)
        if data is None:
            return page(request, "notfound.html", status_code=404, nav=None,
                        what=f"{symbol.upper()} isn't on your watchlist.")
        return page(request, "stock.html", nav="overview", **data)

    @app.get("/screens", response_class=HTMLResponse)
    def screens(request: Request):
        return page(request, "screens.html", nav="screens", **views.screens(get_db()))

    @app.get("/compare", response_class=HTMLResponse)
    def compare(request: Request, symbols: str = "", period: str = "1y"):
        syms = [s for s in symbols[:200].replace(",", " ").upper().split() if sec.valid_symbol(s)][:8]
        data = views.compare(get_db(), syms, period) if syms else {"period": period}
        watched = [i["symbol"] for i in get_db().get_instruments()]
        return page(request, "compare.html", nav="compare", symbols=" ".join(syms),
                    periods=views.PERIOD_NAMES, watched=watched, **data)

    @app.get("/etf", response_class=HTMLResponse)
    def etf(request: Request, a: str = "", b: str = ""):
        a, b = (x.strip().upper() if sec.valid_symbol(x.strip()) else None for x in (a, b))
        data = views.etf_page(get_db(), a, b, funds_provider=funds_provider, provider=provider)
        return page(request, "etf.html", nav="etf", **data)

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request):
        return page(request, "status.html", nav="status", **views.status(get_db()))

    @app.get("/healthz")
    def healthz():
        return JSONResponse({"ok": True})

    # actions --------------------------------------------------------------------
    @app.post("/stock/{symbol}/valuation")
    def rebuild_valuation(symbol: str):
        if not sec.valid_symbol(symbol):
            return back("/", "Not a valid symbol.")
        as_of = views.refresh_valuation(get_db(), symbol, provider=val_provider, fx_provider=fx_provider)
        msg = f"Valuation bands as of {as_of}." if as_of else \
            "Couldn't build valuation bands (Yahoo unreachable or not enough history)."
        return back(f"/stock/{quote(symbol.upper())}", msg)

    @app.post("/zones")
    def add_zone(symbol: str = Form(...), low: str = Form(...), high: str = Form(...), note: str = Form("")):
        from datetime import datetime
        db = get_db()
        inst = db.get_instrument(symbol) if sec.valid_symbol(symbol) else None
        lo, hi = parse_number(low), parse_number(high)
        url = f"/stock/{quote(symbol.upper())}"
        if inst is None:
            return back("/", "That symbol isn't on your watchlist.")
        if lo is None or hi is None or lo <= 0 or hi <= 0:
            return back(url, "Zone not saved: low and high must be prices, e.g. 5500 and 5950.")
        zid = db.add_zone(inst["symbol"], min(lo, hi), max(lo, hi), note.strip()[:80] or None,
                          datetime.now().strftime("%Y-%m-%d"))
        return back(url, f"Zone #{zid} saved. The daily digest will tell you when it closes inside.")

    @app.post("/zones/{zone_id}/delete")
    def delete_zone(zone_id: int, next: str = Form("/")):
        ok = get_db().delete_zone(zone_id)
        url = sec.safe_next(next)
        return back(url, f"Zone #{zone_id} deleted." if ok else f"No zone #{zone_id}.")

    @app.post("/capital")
    def set_capital(amount: str = Form(...), next: str = Form("/")):
        v = parse_number(amount[:30])
        url = sec.safe_next(next)
        if v is None or v <= 0:
            return back(url, "Capital not saved: use an amount like 150jt or 150000000.")
        get_db().set_setting("capital_idr", v)
        return back(url, f"Capital set to {_idr_short(v)}.")

    @app.post("/size", response_class=HTMLResponse)
    def size(request: Request, symbol: str = Form(...), entry: str = Form(...), stop: str = Form(...),
             risk: str = Form(""), max_pos: str = Form("")):
        from app.analytics.sizing import DEFAULT_MAX_POSITION_PCT, DEFAULT_RISK_PCT, size_position
        db = get_db()
        inst = db.get_instrument(symbol) if sec.valid_symbol(symbol) else None
        cap = db.get_setting("capital_idr")
        ctx = {"error": None, "s": None, "inst": inst}
        e, st = parse_number(entry), parse_number(stop)
        r = parse_number(risk.rstrip("%")) if risk.strip() else DEFAULT_RISK_PCT
        m = parse_number(max_pos.rstrip("%")) if max_pos.strip() else DEFAULT_MAX_POSITION_PCT
        if inst is None:
            ctx["error"] = "Not on your watchlist."
        elif cap is None:
            ctx["error"] = "Set your capital first."
        elif None in (e, st, r, m):
            ctx["error"] = "Entry, stop, risk and max must be numbers."
        else:
            try:
                ctx["s"] = size_position(float(cap), e, st, inst["market"], inst["currency"],
                                         usdidr=db.get_latest_fx("USDIDR"), risk_pct=r, max_position_pct=m)
                ctx.update(entry=e, stop=st, risk=r, max_pos=m, capital=float(cap))
            except ValueError as ex:
                ctx["error"] = str(ex)
        return templates.TemplateResponse(request, "_size_result.html", ctx)

    @app.post("/screens/run", response_class=HTMLResponse)
    def run_rule(request: Request, rule: str = Form("")):
        return templates.TemplateResponse(request, "_screen_result.html", views.run_rule(get_db(), rule))

    @app.post("/screens")
    def save_screen(name: str = Form(...), rule: str = Form(...), market: str = Form("")):
        from app.analytics.screener import RuleError, parse_rule
        from app.commands import NAME_RE
        name = name.strip().lower()
        if not NAME_RE.match(name):
            return back("/screens", "Screen names use letters, digits and _ (e.g. cheap_banks).")
        try:
            parsed = parse_rule(rule)
        except RuleError as e:
            return back("/screens", f"Not saved: {e}")
        get_db().save_screen(name, parsed.text, market.upper() if market.upper() in views.MARKETS else None)
        return back("/screens", f"Saved screen {name}. Its matches will appear in the digests.")

    @app.post("/screens/{name}/delete")
    def delete_screen(name: str):
        from app.commands import NAME_RE
        if not NAME_RE.match(name):
            return back("/screens", "Not a valid screen name.")
        ok = get_db().delete_screen(name)
        return back("/screens", f"Deleted {name}." if ok else f"No screen called {name}.")

    return app


def main():
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    problem = config.web_problem()
    if problem:
        raise SystemExit(f"Web dashboard not started: {problem}")
    log.info("Dashboard on http://%s:%s", "127.0.0.1" if config.WEB_HOST in config.LOOPBACK_HOSTS
             else config.WEB_HOST, config.WEB_PORT)
    uvicorn.run(create_app(), host=config.WEB_HOST, port=config.WEB_PORT,
                access_log=False, log_level="warning", server_header=False,
                proxy_headers=False,              # never trust X-Forwarded-For for the lockout
                limit_concurrency=50, timeout_keep_alive=5)


if __name__ == "__main__":
    main()

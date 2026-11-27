"""Security checks: Telegram owner-only access, the web dashboard's guards, input limits.
No network."""
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.analytics.screener import RuleError, parse_rule
from app.web import security as sec
from app.web.server import create_app
from core import config

SAME = {"origin": "http://testserver"}


# --- Telegram ------------------------------------------------------------------------
def update(chat_id, chat_type="private", user_id=None):
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
                           effective_user=SimpleNamespace(id=chat_id if user_id is None else user_id))


def test_bot_only_answers_the_owner_in_private():
    from core.telegram_bot import is_owner
    assert is_owner(update(42), "42")
    assert not is_owner(update(43), "42")
    assert not is_owner(update(42, "group"), "42")             # a group sharing the id
    assert not is_owner(update(42, user_id=7), "42")           # someone else in that chat
    assert not is_owner(SimpleNamespace(effective_chat=None, effective_user=None), "42")


def test_bot_fails_closed_without_chat_id():
    from core.telegram_bot import is_owner
    assert not is_owner(update(42), "")
    assert not is_owner(update(42), "   ")


def test_every_bot_command_is_owner_only():
    """Each registered handler except /start goes through owner_only (functools.wraps
    leaves __wrapped__); /start checks the owner itself."""
    import core.telegram_bot as bot
    from telegram.ext import CallbackQueryHandler, CommandHandler

    class FakeApp:
        def __init__(self):
            self.handlers = []

        def add_handler(self, h):
            self.handlers.append(h)

        def add_error_handler(self, h):
            pass

        def run_polling(self):
            pass

    fake = FakeApp()

    class Builder:
        def __getattr__(self, name):
            return lambda *a, **k: self if name != "build" else fake

    orig_token, orig_builder = bot.TOKEN, bot.Application.builder
    bot.TOKEN, bot.Application.builder = "t", staticmethod(lambda: Builder())
    try:
        bot.main()
    finally:
        bot.TOKEN, bot.Application.builder = orig_token, orig_builder
    assert len(fake.handlers) > 20
    for h in fake.handlers:
        name = next(iter(h.commands)) if isinstance(h, CommandHandler) else "callback"
        if name == "start":
            continue
        assert hasattr(h.callback, "__wrapped__"), f"/{name} is not owner_only"
        assert isinstance(h, (CommandHandler, CallbackQueryHandler))


# --- rule parser ---------------------------------------------------------------------
def test_rule_length_and_nesting_are_capped():
    with pytest.raises(RuleError, match="too long"):
        parse_rule(" and ".join(["pe > 1"] * 100))
    assert parse_rule("((pe > 1))").matches({"pe": 2})
    with pytest.raises(RuleError):   # deep nesting: a clean error, never a crash
        parse_rule("(" * 1000 + "pe > 1" + ")" * 1000)


# --- web: helpers --------------------------------------------------------------------
@pytest.mark.parametrize("url,ok", [
    ("/stock/BBCA", True), ("/", True), ("//evil.example", False), ("/\\evil.example", False),
    ("https://evil.example", False), ("/stock/BBCA?x=1", False), ("javascript:alert(1)", False),
    ("", False), (None, False),
])
def test_safe_next(url, ok):
    assert (sec.safe_next(url) == url) is ok


@pytest.mark.parametrize("sym,ok", [
    ("BBCA", True), ("BRK-B", True), ("^JKSE", True), ("USDIDR=X", True), ("BBCA.JK", True),
    ("", False), ("A" * 16, False), ("<script>", False), ("../etc", False), ("a b", False),
])
def test_valid_symbol(sym, ok):
    assert sec.valid_symbol(sym) is ok


def test_host_check():
    loop = {"127.0.0.1", "localhost", "::1"}
    assert sec.host_ok("127.0.0.1:8000", loop) and sec.host_ok("localhost", loop)
    assert sec.host_ok("[::1]:8000", loop)
    assert not sec.host_ok("evil.example:8000", loop)          # DNS rebinding
    assert not sec.host_ok(None, loop)
    assert sec.host_ok("anything", None)


def test_allowed_hosts_follow_password(monkeypatch):
    monkeypatch.setattr(config, "WEB_PASSWORD", "")
    monkeypatch.setattr(config, "WEB_ALLOWED_HOSTS", [])
    assert sec.allowed_hosts() == {"127.0.0.1", "localhost", "::1"}
    monkeypatch.setattr(config, "WEB_ALLOWED_HOSTS", ["192.168.1.20"])
    assert "192.168.1.20" in sec.allowed_hosts()
    monkeypatch.setattr(config, "WEB_ALLOWED_HOSTS", [])
    monkeypatch.setattr(config, "WEB_PASSWORD", "a-long-password")
    assert sec.allowed_hosts() is None                          # the password protects it


def test_password_rules(monkeypatch):
    monkeypatch.setattr(config, "WEB_HOST", "127.0.0.1")
    monkeypatch.setattr(config, "WEB_PASSWORD", "short")
    assert "at least 12" in config.web_problem()
    monkeypatch.setattr(config, "WEB_PASSWORD", "a-long-password")
    assert config.web_problem() is None


def test_auth_lockout():
    clock = [0.0]
    g = sec.AuthGuard(clock=lambda: clock[0])
    orig = config.WEB_PASSWORD
    config.WEB_PASSWORD = "a-long-password"
    try:
        bad = "Basic " + base64.b64encode(b"x:wrong").decode()
        good = "Basic " + base64.b64encode(b"x:a-long-password").decode()
        assert not g.check("1.2.3.4", None) and not g.locked("1.2.3.4")   # no header: not a guess
        for _ in range(sec.AUTH_MAX_FAILURES):
            assert not g.check("1.2.3.4", bad)
        assert g.locked("1.2.3.4") and not g.locked("5.6.7.8")
        clock[0] += sec.AUTH_LOCKOUT_S + 1
        assert not g.locked("1.2.3.4") and g.check("1.2.3.4", good)
        assert not g.check("1.2.3.4", "Basic !!!notbase64")
    finally:
        config.WEB_PASSWORD = orig


# --- web: the running app ------------------------------------------------------------
@pytest.fixture
def client(db):
    return TestClient(create_app(db=db, extra_hosts=["testserver"]))


def test_security_headers(client):
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-eval" not in csp and "frame-ancestors 'none'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert "server" not in {k.lower() for k in r.headers} or "uvicorn" not in r.headers.get("server", "")
    assert client.get("/static/app.css").headers["cache-control"] == "no-cache"
    # with nosniff, a script served as text/plain would be refused by the browser
    for path in ("/static/app.js", "/static/vendor/htmx-2.0.4.min.js",
                 "/static/vendor/lightweight-charts-4.2.0.js"):
        assert client.get(path).headers["content-type"].startswith("text/javascript"), path
    assert client.get("/static/app.css").headers["content-type"].startswith("text/css")


def test_pages_have_no_inline_scripts(client, db):
    """The CSP blocks inline scripts, so none may be needed."""
    import re
    for url in ("/", "/stock/BBCA", "/screens", "/compare?symbols=BBCA", "/status", "/portfolio"):
        html = client.get(url).text
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert "src=" in tag or 'type="application/json"' in tag, (url, tag)
        assert not re.search(r"\son[a-z]+=", html), url


def test_unknown_host_rejected(db):
    c = TestClient(create_app(db=db), base_url="http://evil.example:8000")
    assert c.get("/").status_code == 400


def test_cross_site_requests_blocked(client):
    assert client.get("/", headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "no-cors",
                                    "sec-fetch-dest": "image"}).status_code == 403
    assert client.get("/etf?a=VOO", headers={"sec-fetch-site": "same-site", "sec-fetch-mode": "cors"}).status_code == 403
    # following a link from another site to a page is fine
    assert client.get("/", headers={"sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate",
                                    "sec-fetch-dest": "document"}).status_code == 200
    # but never a cross-site form post, even as a navigation
    assert client.post("/capital", data={"amount": "1"}, headers={
        "sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}).status_code == 403


def test_other_methods_and_big_bodies_rejected(client):
    assert client.put("/capital", headers=SAME).status_code == 405
    assert client.delete("/zones/1", headers=SAME).status_code == 405
    big = {"amount": "1" * (sec.MAX_BODY + 10)}
    assert client.post("/capital", data=big, headers=SAME).status_code == 413


def test_open_redirect_blocked(client):
    r = client.post("/capital", data={"amount": "150jt", "next": "/\\evil.example"}, headers=SAME,
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/?msg=")


def test_bad_symbols_never_reach_yahoo(db):
    class Boom:
        name = "boom"

        def get_profile(self, symbol):
            raise AssertionError(f"looked up {symbol!r}")
    c = TestClient(create_app(db=db, funds_provider=Boom(), extra_hosts=["testserver"]))
    assert c.get("/etf", params={"a": "<script>"}).status_code == 200
    assert c.get("/stock/" + "A" * 40).status_code == 404


def test_password_required_and_lockout(db, monkeypatch):
    monkeypatch.setattr(config, "WEB_PASSWORD", "a-long-password")
    c = TestClient(create_app(db=db, extra_hosts=["testserver"]))
    assert c.get("/").status_code == 401
    bad = {"authorization": "Basic " + base64.b64encode(b"me:nope").decode()}
    for _ in range(sec.AUTH_MAX_FAILURES):
        assert c.get("/", headers=bad).status_code == 401
    good = {"authorization": "Basic " + base64.b64encode(b"me:a-long-password").decode()}
    assert c.get("/", headers=good).status_code == 429             # locked out for a while


def test_reflected_message_is_escaped_and_capped(client):
    r = client.get("/", params={"msg": "<img src=x onerror=alert(1)>" + "x" * 500})
    assert "<img src=x" not in r.text and "&lt;img" in r.text
    assert "x" * 300 not in r.text

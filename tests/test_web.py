"""Web dashboard: every page renders from a seeded DB, actions work, and the guards
(password, cross-site POST, exposure without a password) hold. No network."""
import base64
import json
import re
from datetime import date

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import commands as cmd
from app.analytics import valuation as val
from app.analytics.fx_returns import compare_closes
from app.web import views
from app.web.server import create_app
from core import config
from tests.test_etf_fx import QQQM_RAW, VOO_RAW, FakeFunds
from tests.test_valuation_tools import FakeVal, inputs

DAYS = pd.bdate_range(end="2026-11-25", periods=400)
SAME = {"origin": "http://testserver"}


def frame(values):
    c = np.asarray(values, dtype=float)
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6}, index=DAYS)


class Prices:
    """Stands in for Yahoo; returns what's stored so nothing is fetched."""
    def __init__(self, closes):
        self.closes = closes

    def get_history(self, instruments, period):
        return {i.symbol: frame(self.closes[i.symbol]) for i in instruments if i.symbol in self.closes}

    def get_fx(self, pair, period):
        return pd.Series(16000.0, index=DAYS)


@pytest.fixture
def seeded(db):
    closes = {"BBCA": np.linspace(8000, 9500, len(DAYS)), "VOO": np.linspace(500, 600, len(DAYS)),
              "QQQM": np.linspace(180, 240, len(DAYS))}
    for sym, c in closes.items():
        db.upsert_price_bars(sym, frame(c))
    db.upsert_fx("USDIDR", pd.Series(np.linspace(15500, 16500, len(DAYS)), index=DAYS))
    db.upsert_fundamentals("BBCA", "2026-11-20", "fake", {"pe": 20.0, "pb": 4.0, "roe": 21.0, "div_yield": 3.0})
    profile = val.build_profile(inputs(close=pd.Series(closes["BBCA"] / 5, index=DAYS)), today=date(2026, 11, 25))
    db.upsert_valuation_bands("BBCA", "2026-11-25", "fake", profile)
    db.add_zone("BBCA", 9000, 9600, "test zone", "2026-11-20")
    return db, closes


@pytest.fixture
def client(seeded):
    db, closes = seeded
    app = create_app(db=db, funds_provider=FakeFunds({"VOO": VOO_RAW, "QQQM": QQQM_RAW}),
                     val_provider=FakeVal(inputs()), provider=Prices(closes), fx_provider=Prices(closes),
                     extra_hosts=["testserver"])
    return TestClient(app)


# --- pages ---------------------------------------------------------------------------
def test_overview_lists_watchlist_zones_and_fx(client):
    r = client.get("/")
    assert r.status_code == 200
    assert 'href="/stock/BBCA"' in r.text and "VOO" in r.text
    assert "USD/IDR" in r.text and "16,500" in r.text
    assert "inside" in r.text            # BBCA last close 9,500 is in the 9,000-9,600 zone


def test_stock_page_chart_data(client):
    r = client.get("/stock/bbca")
    assert r.status_code == 200
    data = json.loads(re.search(r'id="chart-data">(.*?)</script>', r.text, re.S).group(1))
    assert len(data["candles"]) == len(DAYS) and data["idr"] is True
    assert data["candles"][-1] == {"time": "2026-11-25", "open": 9500.0, "high": 9595.0, "low": 9405.0, "close": 9500.0}
    assert len(data["sma200"]) == len(DAYS) - 199
    assert set(data["bands"]) >= {"pb", "pe"} and data["zones"][0]["low"] == 9000
    assert "Valuation vs its own history" in r.text and "Add zone" in r.text


def test_etf_stock_page_has_no_valuation(client):
    r = client.get("/stock/VOO")
    assert r.status_code == 200 and "Valuation vs its own history" not in r.text


def test_unknown_symbol_is_404(client):
    r = client.get("/stock/NOPE")
    assert r.status_code == 404 and "NOPE isn&#39;t on your watchlist" in r.text


def test_compare_page(client):
    r = client.get("/compare", params={"symbols": "BBCA VOO", "period": "1y"})
    assert r.status_code == 200 and "In IDR" in r.text
    series = json.loads(re.search(r'id="cmp-data">(.*?)</script>', r.text, re.S).group(1))
    assert set(series) == {"BBCA", "VOO"} and series["VOO"]["idr"][0]["value"] == 100
    assert client.get("/compare", params={"symbols": "NOPE"}).status_code == 200


def test_etf_page_and_overlap(client):
    r = client.get("/etf", params={"a": "voo", "b": "qqqm"})
    assert r.status_code == 200
    assert "VOO vs QQQM" in r.text and "Top-holdings overlap" in r.text and "Vanguard" in r.text


def test_screens_and_status_pages(client):
    assert "value_idx" in client.get("/screens").text
    assert "Daily digests" in client.get("/status").text
    assert client.get("/healthz").json() == {"ok": True}


# --- actions -------------------------------------------------------------------------
def test_add_and_delete_zone(client, seeded):
    db, _ = seeded
    r = client.post("/zones", data={"symbol": "BBCA", "low": "8.500", "high": "8,800", "note": "x"},
                    headers=SAME, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/stock/BBCA?msg=")
    z = [z for z in db.get_zones(symbol="BBCA") if z["note"] == "x"][0]
    assert (z["low"], z["high"]) == (8500, 8800)
    client.post(f"/zones/{z['id']}/delete", data={"next": "//evil.example"}, headers=SAME)
    assert all(x["id"] != z["id"] for x in db.get_zones())
    bad = client.post("/zones", data={"symbol": "BBCA", "low": "abc", "high": "1"}, headers=SAME)
    assert "Zone not saved" in bad.text


def test_size_fragment(client, seeded):
    db, _ = seeded
    assert "Set your capital first" in client.post(
        "/size", data={"symbol": "BBCA", "entry": "9500", "stop": "9000"}, headers=SAME).text
    client.post("/capital", data={"amount": "150jt"}, headers=SAME)
    assert float(db.get_setting("capital_idr")) == pytest.approx(150e6)
    r = client.post("/size", data={"symbol": "BBCA", "entry": "9500", "stop": "9000", "risk": "1%"}, headers=SAME)
    assert "Buy <b>15 lots (1,500 shares)</b>" in r.text      # 1.5jt risk / 500 = 3,000 sh, capped at 10%
    assert "Capped" in r.text


def test_rule_runner_and_saving_screens(client, seeded):
    db, _ = seeded
    r = client.post("/screens/run", data={"rule": "roe > 20 and pb < 5"}, headers=SAME)
    assert "BBCA" in r.text and "1</b> of" in r.text
    assert "warnbox" in client.post("/screens/run", data={"rule": "roe >>"}, headers=SAME).text
    client.post("/screens", data={"name": "rich", "rule": "roe > 20", "market": "IDX"}, headers=SAME)
    assert db.get_screen("rich")["market"] == "IDX"
    client.post("/screens/rich/delete", headers=SAME)
    assert db.get_screen("rich") is None


def test_rebuild_valuation(client, seeded):
    db, _ = seeded
    r = client.post("/stock/BBCA/valuation", headers=SAME, follow_redirects=False)
    assert r.status_code == 303 and "Valuation%20bands%20as%20of" in r.headers["location"]
    # rebuilt from the fake provider's inputs (bvps 500), not the seeded profile
    assert db.get_valuation_bands("BBCA")["current"]["bvps"] == pytest.approx(500)


# --- guards --------------------------------------------------------------------------
def test_cross_site_posts_are_blocked(client, seeded):
    db, _ = seeded
    n = len(db.get_zones())
    form = {"symbol": "BBCA", "low": "1", "high": "2"}
    assert client.post("/zones", data=form).status_code == 403                        # no origin at all
    assert client.post("/zones", data=form, headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/zones", data=form, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert len(db.get_zones()) == n
    assert client.post("/zones", data=form, headers={"sec-fetch-site": "same-origin"},
                       follow_redirects=False).status_code == 303


def test_password(client, monkeypatch):
    monkeypatch.setattr(config, "WEB_PASSWORD", "s3cret-passphrase")
    assert client.get("/").status_code == 401
    good = base64.b64encode(b"me:s3cret-passphrase").decode()
    bad = base64.b64encode(b"me:wrong").decode()
    assert client.get("/", headers={"authorization": f"Basic {bad}"}).status_code == 401
    assert client.get("/", headers={"authorization": f"Basic {good}"}).status_code == 200


def test_dashboard_never_exposed_without_password(monkeypatch):
    from app import run
    monkeypatch.setattr(config, "WEB_PASSWORD", "")
    monkeypatch.setattr(config, "WEB_HOST", "127.0.0.1")
    assert config.web_problem() is None and "web" in run.services_to_run()
    monkeypatch.setattr(config, "WEB_HOST", "0.0.0.0")
    assert "WEB_PASSWORD" in config.web_problem()
    assert set(run.services_to_run()) == {"bot", "worker"}
    monkeypatch.setattr(config, "WEB_PASSWORD", "a-long-password")
    assert "web" in run.services_to_run()
    monkeypatch.setattr(config, "WEB_ENABLED", False)
    assert "web" not in run.services_to_run()


# --- shared numbers ------------------------------------------------------------------
def test_band_prices_flip_for_yield():
    p = val.build_profile(inputs(), today=date(2025, 12, 31))
    for metric in ("pb", "pe", "yield"):
        prices = views.band_prices(p, metric)
        assert prices[10] < prices[25] < prices[50] < prices[75] < prices[90], metric
    r = {x.metric: x for x in val.assess(p, 1500)}
    assert views.band_prices(p, "pb")[25] == pytest.approx(r["pb"].price_cheap)
    assert views.band_prices(p, "yield")[25] == pytest.approx(r["yield"].price_cheap)


def test_compare_growth_matches_returns(seeded):
    db, _ = seeded
    found = [(s, c, db.get_price_history(s)["Close"]) for s, c in (("BBCA", "IDR"), ("VOO", "USD"))]
    cmp = compare_closes(found, db.get_fx_history("USDIDR"), "1y")
    for r in cmp.results:
        g = cmp.growth[r.symbol]
        assert g["idr"].iloc[-1] - 100 == pytest.approx(r.idr)
        assert g["usd"].iloc[-1] - 100 == pytest.approx(r.usd)
        assert g["local"].iloc[-1] - 100 == pytest.approx(r.local)
    # the Telegram command still gives the same numbers
    text = cmd.cmd_compare(db, ["BBCA", "VOO", "1y"], provider=Prices({}), now=pd.Timestamp("2026-11-26 12:00", tz="UTC"))
    assert f"{cmp.results[1].idr:+.1f}" in text

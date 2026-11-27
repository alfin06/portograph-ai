"""Portfolio ledger: average cost, realised profit, IDR with the exchange rate, guards
against impossible sales, Telegram commands and the dashboard page. No network."""
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import commands as c
from app.analytics import portfolio as pf
from app.web.server import create_app

TODAY = date(2026, 11, 27)
SAME = {"origin": "http://testserver"}


def bars(closes, start="2026-11-24"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": [x * 1.02 for x in closes], "Low": [x * 0.98 for x in closes],
                         "Close": closes, "Volume": [1.0] * len(closes)}, index=idx)


@pytest.fixture
def pdb(db):
    """Starter watchlist (BBCA, VOO, ...) with three closes each and a USD/IDR history."""
    db.upsert_price_bars("BBCA", bars([9000.0, 9100.0, 9200.0]))
    db.upsert_price_bars("VOO", bars([530.0, 535.0, 540.0]))
    db.upsert_fx("USDIDR", pd.Series([16000.0, 16500.0, 16600.0],
                                     index=pd.to_datetime(["2026-10-01", "2026-11-20", "2026-11-26"])))
    return db


def txn(i, sym, day, kind, shares=0, price=0, fee=0, amount=None, fx=1.0, market="IDX", ccy="IDR"):
    return {"id": i, "symbol": sym, "date": day, "kind": kind, "shares": shares, "price": price, "fee": fee,
            "amount": amount, "fx_idr": fx, "market": market, "currency": ccy, "asset_type": "STOCK"}


# --- the maths ----------------------------------------------------------------------------
def test_average_cost_and_realised_profit():
    p = pf.replay([
        txn(1, "BBCA", "2026-01-05", "BUY", 1000, 9000, fee=13500),
        txn(2, "BBCA", "2026-02-05", "BUY", 1000, 8000, fee=12000),
        txn(3, "BBCA", "2026-03-05", "SELL", 500, 9500, fee=11875),
    ])["BBCA"]
    avg = (9_000_000 + 13500 + 8_000_000 + 12000) / 2000
    assert p.shares == 1500
    assert p.avg_cost == pytest.approx(avg)                   # a sale doesn't change the average
    assert p.realised == pytest.approx(500 * 9500 - 11875 - 500 * avg)
    assert p.cost == pytest.approx(1500 * avg)


def test_selling_everything_closes_the_position():
    p = pf.replay([txn(1, "X", "2026-01-05", "BUY", 300, 100), txn(2, "X", "2026-01-06", "SELL", 300, 110)])["X"]
    assert not p.open and p.cost == 0 and p.realised == pytest.approx(3000)


def test_order_is_by_date_not_entry_order():
    # the sell was typed first but happened later: still valid
    pf.replay([txn(2, "X", "2026-02-01", "SELL", 100, 120), txn(1, "X", "2026-01-01", "BUY", 100, 100)])


def test_cannot_sell_more_than_held():
    with pytest.raises(pf.PortfolioError, match="more than"):
        pf.replay([txn(1, "X", "2026-01-05", "BUY", 100, 100), txn(2, "X", "2026-01-04", "SELL", 100, 100)])


def test_idr_profit_splits_into_price_and_rupiah():
    p = pf.replay([txn(1, "VOO", "2026-10-02", "BUY", 2, 500, fx=16000, market="US", ccy="USD")])["VOO"]
    p.close, p.fx_now = 550.0, 17000.0
    assert p.cost_idr == 16_000_000
    assert p.unrealised == pytest.approx(100)                          # USD
    assert p.unrealised_idr == pytest.approx(2 * 550 * 17000 - 16_000_000)
    assert p.fx_effect_idr == pytest.approx(1000 * 1000)               # $1,000 cost x Rp 1,000 move
    assert p.unrealised_idr == pytest.approx(p.unrealised * 17000 + p.fx_effect_idr)


def test_dividends_are_income_not_cost():
    p = pf.replay([txn(1, "X", "2026-01-05", "BUY", 100, 100), txn(2, "X", "2026-03-01", "DIV", amount=500)])["X"]
    assert p.income == 500 and p.avg_cost == 100


# --- commands ---------------------------------------------------------------------------
def test_buy_in_lots_with_estimated_fee(pdb):
    out = c.cmd_buy(pdb, ["BBCA", "10", "8950", "20/11"], today=TODAY)
    assert "✅" in out and "10 lots" in out and "2026-11-20" in out
    t = pdb.get_transactions("BBCA")[0]
    assert t["shares"] == 1000 and t["fee"] == pytest.approx(8_950_000 * 0.0015)
    assert pdb.get_instrument("BBCA")["is_holding"] == 1


def test_us_buy_stores_the_rate_of_that_day(pdb):
    c.cmd_buy(pdb, ["VOO", "1.5", "500", "2026-10-02", "fee=0"], today=TODAY)
    t = pdb.get_transactions("VOO")[0]
    assert t["shares"] == 1.5 and t["fee"] == 0 and t["fx_idr"] == 16000


@pytest.mark.parametrize("args,why", [
    (["BBCA", "1.5", "8950"], "whole lots"),
    (["BBCA", "10", "-5"], "positive"),
    (["BBCA", "10", "8950", "2027-01-01"], "future"),
    (["BBCA", "10", "8950", "31/02/2026"], "date"),
    (["NOPE", "10", "100"], "watchlist"),
    (["BBCA", "10", "8950", "fee=abc"], "fee"),
])
def test_bad_trades_are_refused(pdb, args, why):
    out = c.cmd_buy(pdb, args, today=TODAY)
    assert "❌" in out and why in out
    assert pdb.get_transactions() == []


def test_oversell_and_unsafe_delete_are_refused(pdb):
    c.cmd_buy(pdb, ["BBCA", "10", "8950", "20/11"], today=TODAY)
    assert "more than" in c.cmd_sell(pdb, ["BBCA", "11", "9000"], today=TODAY)
    assert "✅" in c.cmd_sell(pdb, ["BBCA", "10", "9000"], today=TODAY)
    assert pdb.get_instrument("BBCA")["is_holding"] == 0             # sold out
    assert "❌" in c.cmd_deltxn(pdb, ["1"])                          # the sale needs that buy
    assert "✅" in c.cmd_deltxn(pdb, ["2"]) and "✅" in c.cmd_deltxn(pdb, ["1"])


def test_portfolio_message(pdb):
    assert "No positions" in c.cmd_portfolio(pdb)
    c.cmd_buy(pdb, ["BBCA", "10", "8950", "20/11"], today=TODAY)
    c.cmd_buy(pdb, ["VOO", "1.5", "500", "2026-10-02", "fee=0"], today=TODAY)
    c.cmd_div(pdb, ["BBCA", "250rb"], today=TODAY)
    out = c.cmd_portfolio(pdb)
    assert "<pre>" in out and "BBCA" in out and "VOO" in out
    assert "of which the rupiah" in out                               # US holding in IDR
    assert "dividends Rp 250 rb" in out
    assert "BBRI" in out                                              # seeded holding not recorded yet


def test_portfolio_totals(pdb):
    c.cmd_buy(pdb, ["BBCA", "10", "8950", "20/11", "fee=0"], today=TODAY)
    c.cmd_buy(pdb, ["VOO", "2", "500", "2026-10-02", "fee=0"], today=TODAY)
    port = pf.build(pdb)
    assert port.value_idr == pytest.approx(1000 * 9200 + 2 * 540 * 16600)
    assert port.cost_idr == pytest.approx(8_950_000 + 1000 * 16000)
    assert port.day_change_idr == pytest.approx(1000 * 100 + 2 * 5 * 16600)
    assert sum(w for _, w in port.by_currency) == pytest.approx(100)
    assert port.positions[0].symbol == "VOO"                          # largest first


def test_concentration_warning_skips_etfs(pdb):
    c.cmd_buy(pdb, ["BBCA", "10", "8950", "20/11"], today=TODAY)
    c.cmd_buy(pdb, ["VOO", "100", "500", "2026-10-02"], today=TODAY)
    warnings = " ".join(pf.build(pdb).warnings)
    assert "VOO" not in warnings
    pdb2_only = [p for p in pf.build(pdb).positions if p.symbol == "BBCA"][0]
    assert pdb2_only.weight < 20


def test_txns_and_price(pdb):
    c.cmd_buy(pdb, ["BBCA", "10", "8950", "20/11"], today=TODAY)
    assert "#1" in c.cmd_txns(pdb, []) and "BBCA" in c.cmd_txns(pdb, ["bbca"])

    class NoNet:
        def get_history(self, *a, **k):
            return {}
    out = c.cmd_price(pdb, ["BBCA"], provider=NoNet())
    assert "9,200" in out and "You hold 10 lots" in out


@pytest.mark.parametrize("text,want", [
    ("2026-11-20", "2026-11-20"), ("20/11/2026", "2026-11-20"), ("20/11", "2026-11-20"),
    ("28/12", "2025-12-28"), ("today", "2026-11-27"), ("yesterday", "2026-11-26"),
    ("2026-11-28", None), ("1999-01-01", None), ("soon", None),
])
def test_parse_date(text, want):
    assert c.parse_date(text, TODAY) == want


# --- dashboard ----------------------------------------------------------------------------
@pytest.fixture
def client(pdb):
    return TestClient(create_app(db=pdb, extra_hosts=["testserver"]))


def test_portfolio_page_and_forms(client, pdb):
    assert client.get("/portfolio").status_code == 200
    r = client.post("/portfolio/txn", data={"kind": "BUY", "symbol": "BBCA", "qty": "10", "price": "8950",
                                            "day": "2026-11-20"}, headers=SAME, follow_redirects=False)
    assert r.status_code == 303 and "Saved" in r.headers["location"]
    client.post("/portfolio/txn", data={"kind": "DIV", "symbol": "BBCA", "amount": "250rb"}, headers=SAME)
    html = client.get("/portfolio").text
    assert "BBCA" in html and "Dividend" in html and "Positions" in html
    assert "Portfolio" in client.get("/").text                       # strip on the overview

    r = client.post("/portfolio/txn", data={"kind": "SELL", "symbol": "BBCA", "qty": "99", "price": "1"},
                    headers=SAME, follow_redirects=False)
    assert "Not%20saved" in r.headers["location"]
    r = client.post("/portfolio/txn", data={"kind": "BUY", "symbol": "<x>", "qty": "1", "price": "1"},
                    headers=SAME, follow_redirects=False)
    assert "Not%20saved" in r.headers["location"]
    r = client.post("/portfolio/txn/1/delete", headers=SAME, follow_redirects=False)
    assert "Deleted" in r.headers["location"] or "deleted" in r.headers["location"].lower()
    assert len(pdb.get_transactions()) == 1                           # the dividend is left


def test_portfolio_forms_need_same_origin(client, pdb):
    r = client.post("/portfolio/txn", data={"kind": "BUY", "symbol": "BBCA", "qty": "1", "price": "1"})
    assert r.status_code == 403 and pdb.get_transactions() == []

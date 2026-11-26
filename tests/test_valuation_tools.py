"""Valuation bands, buy zones and position sizing (no network)."""
from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app import commands as cmd
from app.analytics import sizing, valuation as val, zones
from app.reports.digest import format_zones
from app.symbols import Instrument

DAYS = pd.bdate_range("2021-01-01", "2025-12-31")
BBCA = Instrument("BBCA", "IDX", "STOCK", "IDR")


def inputs(close=None, fin="IDR", trade="IDR", equity=500e6, income=100e6, shares=1e6, divs=50.0):
    years = [f"{y}-12-31" for y in range(2020, 2026)]
    close = close if close is not None else pd.Series(np.linspace(1000, 2000, len(DAYS)), index=DAYS)
    d = pd.Series(divs, index=pd.to_datetime([f"{y}-06-01" for y in range(2020, 2026)])) if divs else pd.Series(dtype=float)
    return {"currency": trade, "financial_currency": fin, "close": close, "dividends": d,
            "annual": {"equity": {y: equity for y in years}, "shares": {y: shares for y in years},
                       "income": {y: income for y in years}},
            "quarterly": {"equity": {}, "shares": {}, "income": {}}}


# --- valuation -------------------------------------------------------------------------
def test_timeline_waits_until_figures_are_public():
    tl = val.timeline(inputs())
    assert tl.index[0] == pd.Timestamp("2021-03-31")          # 2020 annual + 90 days
    m = val.daily_metrics(inputs()["close"], tl, inputs()["dividends"])
    assert np.isnan(m.loc["2021-03-30", "pb"]) and m.loc["2021-03-31", "pb"] == pytest.approx(
        m.loc["2021-03-31", "bvps"] and inputs()["close"]["2021-03-31"] / 500)
    assert m.loc["2023-06-02", "yield"] == pytest.approx(50 / inputs()["close"]["2023-06-02"] * 100)


def test_quarterly_ttm_needs_four_quarters():
    q = {"2025-03-31": 10.0, "2025-06-30": 10.0, "2025-09-30": 10.0, "2025-12-31": 10.0}
    assert val._ttm_income(q, "2025-12-31") == 40
    assert val._ttm_income({k: v for k, v in q.items() if k != "2025-06-30"}, "2025-12-31") is None


def test_profile_bands_and_reading():
    p = val.build_profile(inputs(), today=date(2026, 1, 5))
    assert p["current"] == {"bvps": 500, "eps": 100, "ttm_div": 50}
    pb = p["bands"]["pb"]
    assert 2.05 < pb["q"][0] < 2.15 and pb["q"][-1] == pytest.approx(4.0)  # price/500, from Mar 2021
    assert p["bands"]["yield"]["q"][0] > 2.4          # no fake 0% days between yearly payments
    cheap = {r.metric: r for r in val.assess(p, 1050)}
    assert cheap["pb"].cheap_side == "cheap" and cheap["pe"].cheap_side == "cheap"
    assert cheap["yield"].cheap_side == "cheap"                                # high yield
    assert cheap["pb"].price_cheap == pytest.approx(val.at(pb, 25) * 500)
    assert cheap["pb"].price_deep < cheap["pb"].price_cheap < cheap["pb"].price_typical
    dear = {r.metric: r for r in val.assess(p, 2100)}
    assert dear["pb"].cheap_side == "expensive" and dear["pb"].rank == 100


def test_profile_converts_reporting_currency():
    fx = pd.Series(16000.0, index=pd.date_range("2015-01-01", "2026-01-31"))
    p = val.build_profile(inputs(fin="USD", equity=50e3, income=10e3), fx=fx, today=date(2026, 1, 5))
    assert p["current"]["bvps"] == pytest.approx(800)                  # 0.05 USD x 16,000
    assert p["current"]["eps"] == pytest.approx(160)
    # no FX: P/B and P/E can't be converted, but yield still works (paid in the trading currency)
    for fin in ("USD", "EUR"):
        p = val.build_profile(inputs(fin=fin), fx=None, today=date(2026, 1, 5))
        assert p["bands"]["pb"] is None and p["bands"]["pe"] is None and p["bands"]["yield"] is not None
        assert p["current"]["bvps"] is None


def test_no_dividends_means_no_yield_band():
    p = val.build_profile(inputs(divs=None), today=date(2026, 1, 5))
    assert p["bands"]["yield"] is None
    assert [r.metric for r in val.assess(p, 1500)] == ["pb", "pe"]


def test_roe_history_and_warning():
    assert [r["roe"] for r in val.roe_history(inputs())] == [20.0] * 6
    assert val.roe_warning([{"roe": 20}, {"roe": 21}, {"roe": 19}]) is None
    assert "fallen to 10.0%" in val.roe_warning([{"roe": 20}, {"roe": 20}, {"roe": 10}])


class FakeVal:
    name = "fake"

    def __init__(self, data):
        self.data, self.calls = data, 0

    def get_inputs(self, yahoo_symbol):
        self.calls += 1
        return self.data


def test_profile_cache(db):
    fake = FakeVal(inputs())
    val.get_profile(db, BBCA, provider=fake, today=date(2026, 1, 5))
    val.get_profile(db, BBCA, provider=fake, today=date(2026, 1, 9))
    assert fake.calls == 1
    stale = val.get_profile(db, BBCA, provider=FakeVal(None), today=date(2026, 2, 1))
    assert stale["as_of"] == "2026-01-05"


class NoPrices:
    def get_history(self, instruments, period):
        return {}

    def get_fx(self, pair, period):
        return pd.Series(dtype=float)


def _watch(db, sym="BBCA", market="IDX", kind="STOCK", ccy="IDR", close=1500.0):
    db.add_instrument(sym, market, kind, ccy)
    idx = pd.bdate_range(end="2025-12-31", periods=5)
    db.upsert_price_bars(sym, pd.DataFrame({"Open": close, "High": close, "Low": close,
                                            "Close": close, "Volume": 0.0}, index=idx))


def test_value_command(db):
    _watch(db)
    out = cmd.cmd_value(db, ["bbca"], provider=NoPrices(), val_provider=FakeVal(inputs()))
    # synthetic dividends end in 2025, so measured from today there is no yield row
    assert "valuation vs its own last" in out and "P/B" in out and "P/E" in out
    assert "/zone BBCA" in out or "already below" in out
    _watch(db, "VOO", "US", "ETF", "USD", 600)
    assert "/etf VOO" in cmd.cmd_value(db, ["VOO"])
    assert "isn't on your watchlist" in cmd.cmd_value(db, ["NOPE"])


# --- zones -----------------------------------------------------------------------------
def test_zone_states():
    assert zones.state_of(100, 90, 110) == "inside"
    assert zones.state_of(80, 90, 110) == "below" and zones.state_of(120, 90, 110) == "above"
    assert zones.distance_pct(120, 90, 110) == pytest.approx(-8.33, abs=0.01)


def test_zone_alerts_once_then_on_fall_through(db):
    _watch(db)
    db.add_zone("BBCA", 5500, 5950, "cheap P/B", "2025-12-01")
    day = pd.Timestamp("2025-12-31")
    snap = lambda c: [SimpleNamespace(symbol="BBCA", date=day, close=c)]

    first = zones.check(db, "IDX", snap(5800), day)
    assert [(e.state, e.new) for e in first] == [("inside", True)]
    assert [(e.state, e.new) for e in zones.check(db, "IDX", snap(5700), day)] == [("inside", False)]
    assert [(e.state, e.new) for e in zones.check(db, "IDX", snap(5000), day, record=False)] == [("below", True)]
    assert zones.check(db, "IDX", snap(5000), day)[0].new                  # record=False changed nothing
    assert zones.check(db, "IDX", snap(7000), day) == []
    assert zones.check(db, "US", snap(5800), day) == []                    # other market
    text = format_zones(first)
    assert "<b>BBCA</b> 5,800 in zone 5,500–5,950 🆕 · cheap P/B" in text


def test_zone_commands(db):
    _watch(db)
    assert "Usage" in cmd.cmd_zone(db, ["BBCA", "5500"])
    out = cmd.cmd_zone(db, ["BBCA", "1.600", "1,400", "dip"], provider=NoPrices())
    assert "Zone #1: BBCA 1,400–1,600" in out and "inside it now" in out   # swapped to low-high
    assert "#1 <b>BBCA</b> 1,400–1,600 · now 1,500, inside · dip" in cmd.cmd_zones(db)
    assert "deleted" in cmd.cmd_delzone(db, ["#1"]) and "No zone #1" in cmd.cmd_delzone(db, ["1"])


# --- sizing ----------------------------------------------------------------------------
def test_size_idx_lots_and_cap():
    s = sizing.size_position(150e6, 5800, 5400, "IDX", "IDR")
    assert s.shares == 2500 and s.capped                                   # risk allows 3,700
    assert s.loss_at_stop_idr == 2500 * 400 and s.weight_pct == pytest.approx(9.67, abs=0.01)
    s = sizing.size_position(150e6, 5800, 5400, "IDX", "IDR", max_position_pct=50)
    assert s.shares == 3700 and not s.capped and s.loss_pct <= 1


def test_size_usd_and_errors():
    s = sizing.size_position(150e6, 100, 95, "US", "USD", usdidr=16000)
    assert s.shares == 9 and s.capped                                       # risk alone: 18
    assert s.position_idr == 9 * 100 * 16000
    tiny = sizing.size_position(10e6, 600, 500, "US", "USD", usdidr=16000, risk_pct=0.5)
    assert tiny.shares == 0 and 0 < tiny.fractional < 1
    for bad in (dict(stop=5900), dict(stop=0)):
        with pytest.raises(ValueError):
            sizing.size_position(150e6, 5800, bad["stop"], "IDX", "IDR")
    with pytest.raises(ValueError):
        sizing.size_position(150e6, 100, 95, "US", "USD", usdidr=None)


def test_parse_number():
    cases = {"5500": 5500, "5,500": 5500, "5.500": 5500, "5500.5": 5500.5, "12.75": 12.75,
             "150jt": 150e6, "1.5m": 1.5e6, "2b": 2e9, "Rp150juta": 150e6, "1,234,567": 1234567}
    for text, want in cases.items():
        assert cmd.parse_number(text) == pytest.approx(want), text
    assert cmd.parse_number("abc") is None and cmd.parse_number("10x") is None


def test_capital_and_size_commands(db):
    _watch(db, close=5800)
    assert "Set your capital first" in cmd.cmd_size(db, ["BBCA", "5800", "5400"])
    assert "Rp 150.0 juta" in cmd.cmd_capital(db, ["150jt"])
    assert "Rp 150.0 juta" in cmd.cmd_capital(db, [])
    out = cmd.cmd_size(db, ["BBCA", "now", "5400", "1%"], provider=NoPrices())
    assert "Buy <b>25 lots (2,500 shares)</b>" in out and "Capped by the 10% max" in out
    assert "stop must be below" in cmd.cmd_size(db, ["BBCA", "5800", "6000"])
    assert "Usage" in cmd.cmd_size(db, ["BBCA", "5800"])
